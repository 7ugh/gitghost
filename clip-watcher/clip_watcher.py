#!/usr/bin/env python3
"""Record a Twitch stream, watch chat volume, cut vertical clips at spikes, caption with Whisper."""
import argparse, collections, os, queue, re, signal, socket, statistics, subprocess
import sys, threading, time

BEFORE, AFTER, SAFETY = 20, 10, 4   # clip = 20s before spike, 10s after (+ flush margin)
WINDOW_S = 180                      # baseline: median of last 3 min
MIN_HISTORY = 6                     # buckets of history needed before judging spikes


class SpikeDetector:
    """Counts messages in fixed buckets; flags a bucket that beats factor x trailing median."""

    def __init__(self, bucket=10, factor=2.5, min_msgs=15, window=WINDOW_S):
        self.bucket, self.factor, self.min_msgs = bucket, factor, min_msgs
        self.n_hist = max(1, window // bucket)
        self.counts = collections.Counter()
        self.last_closed = None

    def add(self, ts):
        self.counts[int(ts // self.bucket)] += 1

    def close_until(self, now):
        """Close every bucket that ended before `now`; yield (end_time, count, median) for spikes."""
        newest = int(now // self.bucket) - 1
        if self.last_closed is None:
            self.last_closed = newest - 1 if not self.counts else min(self.counts) - 1
        while self.last_closed < newest:
            self.last_closed += 1
            b = self.last_closed
            prev = [self.counts.get(i, 0) for i in range(b - self.n_hist, b)]
            n = self.counts.get(b, 0)
            med = statistics.median(prev)
            seen = b - min(self.counts) if self.counts else 0
            if seen >= MIN_HISTORY and n >= self.min_msgs and n >= self.factor * med:
                yield (b + 1) * self.bucket, n, med
            for old in [k for k in self.counts if k < b - self.n_hist]:
                del self.counts[old]


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def chat_reader(channel, on_msg, stop):
    """Anonymous Twitch IRC (justinfan, no token). Reconnects on drop."""
    while not stop.is_set():
        try:
            with socket.create_connection(("irc.chat.twitch.tv", 6667), timeout=30) as s:
                s.settimeout(60)
                s.sendall(f"NICK justinfan12345\r\nJOIN #{channel.lower()}\r\n".encode())
                log(f"chat: joined #{channel}")
                buf = b""
                while not stop.is_set():
                    data = s.recv(4096)
                    if not data:
                        break
                    buf += data
                    *lines, buf = buf.split(b"\r\n")
                    for ln in lines:
                        if ln.startswith(b"PING"):
                            s.sendall(b"PONG :tmi.twitch.tv\r\n")
                        elif b" PRIVMSG " in ln:
                            on_msg(time.time())
        except (OSError, socket.timeout) as e:
            if not stop.is_set():
                log(f"chat: {e}; reconnecting in 5s")
                stop.wait(5)


def run(cmd):
    return subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)


def cut_clip(src, rec_start, spike_t, channel, outdir, ready_q):
    start = max(0.0, spike_t - BEFORE - rec_start)
    end_wall = spike_t + AFTER + SAFETY
    time.sleep(max(0, end_wall - time.time()))     # wait for the "after" part to be recorded
    name = f"{channel}_{time.strftime('%Y%m%d_%H%M%S', time.localtime(spike_t))}.mp4"
    out = os.path.join(outdir, name)
    r = run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{start:.2f}", "-i", src,
             "-t", str(BEFORE + AFTER),
             "-vf", "crop='trunc(ih*9/16/2)*2':ih,scale=1080:1920",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
             "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", out])
    if r.returncode:
        log("ffmpeg failed:", r.stderr.strip()[-300:])
        return
    log(f"clip: {out}")
    ready_q.put(out)


def whisper_worker(q, outdir, model):
    while True:
        clip = q.get()
        if clip is None:
            return
        r = run(["whisper", clip, "--model", model, "--output_format", "srt",
                 "--output_dir", outdir, "--fp16", "False"])
        log(f"srt: {os.path.splitext(clip)[0]}.srt" if not r.returncode
            else f"whisper failed: {r.stderr.strip()[-300:]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("channel")
    ap.add_argument("--min", type=int, default=15, help="min messages in a bucket to count as a spike (use 5 for quiet chats)")
    ap.add_argument("--factor", type=float, default=2.5, help="spike = factor x median of last 3 min")
    ap.add_argument("--bucket", type=int, default=10, help="bucket length in seconds")
    ap.add_argument("--model", default="base", help="whisper model")
    ap.add_argument("--quality", default="best", help="streamlink quality")
    a = ap.parse_args()
    channel = a.channel.lstrip("#").lower()
    if not re.fullmatch(r"\w{1,25}", channel):
        sys.exit("bad channel name")

    rec = os.path.abspath(f"rec/{channel}_{time.strftime('%Y%m%d_%H%M%S')}.ts")
    sl = subprocess.Popen(["streamlink", "--twitch-disable-ads", "-o", rec,
                           f"twitch.tv/{channel}", a.quality],
                          stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())

    # wait for first bytes: that moment is t=0 of the recording
    rec_start = None
    while rec_start is None and not stop.is_set():
        if sl.poll() is not None:
            sys.exit(f"streamlink exited: {sl.stderr.read().strip()[-300:] or 'is the channel live?'}")
        if os.path.exists(rec) and os.path.getsize(rec) > 0:
            rec_start = time.time()
        time.sleep(0.2)
    log(f"recording -> {rec}")

    det, lock = SpikeDetector(a.bucket, a.factor, a.min), threading.Lock()
    def on_msg(ts):
        with lock:
            det.add(ts)
    threading.Thread(target=chat_reader, args=(channel, on_msg, stop), daemon=True).start()

    wq = queue.Queue()
    wt = threading.Thread(target=whisper_worker, args=(wq, "clips", a.model))
    wt.start()
    cutters, last_spike = [], 0
    try:
        while not stop.is_set():
            if sl.poll() is not None:
                log("stream ended")
                break
            with lock:
                spikes = list(det.close_until(time.time()))
            for t, n, med in spikes:
                if t - last_spike < BEFORE + AFTER:      # same burst, already clipped
                    continue
                last_spike = t
                log(f"SPIKE: {n} msgs/{a.bucket}s vs median {med:g}")
                th = threading.Thread(target=cut_clip, args=(rec, rec_start, t, channel, "clips", wq))
                th.start(); cutters.append(th)
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        if sl.poll() is None:
            sl.terminate()
        log("finishing pending clips...")
        for th in cutters:
            th.join()
        wq.put(None)
        wt.join()


if __name__ == "__main__":
    main()
