"""Narration. Backends (auto picks the first available): piper > macOS say > espeak-ng > ffmpeg-flite (robotic, test fallback).
Output: mono 44.1kHz WAV per scene + word timings (proportional estimate; swap in Whisper alignment later if you like).
"""
from __future__ import annotations
import re, subprocess, wave, pathlib, shutil
import numpy as np
from . import config, util

SR = 44100


def _piper_models():
    return sorted((config.HOME / "piper").glob("*.onnx"))


def list_voices() -> list[dict]:
    v = []
    for m in _piper_models():
        v.append({"backend": "piper", "id": m.stem, "name": f"Piper {m.stem}"})
    if util.have("say"):
        out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True).stdout
        for line in out.splitlines():
            mm = re.match(r"^(.+?)\s{2,}(\w\w[_-]\w\w)\s+#", line)
            if mm and mm.group(2).lower().startswith("en"):
                v.append({"backend": "say", "id": mm.group(1).strip(), "name": f"macOS {mm.group(1).strip()} ({mm.group(2)})"})
    if util.have("espeak-ng"):
        for vid in ("en-us", "en-gb", "en-us+f3", "en-us+m3"):
            v.append({"backend": "espeak", "id": vid, "name": f"espeak-ng {vid}"})
    if util.have("ffmpeg") and " flite " in subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True).stdout:
        for vid in ("slt", "awb", "kal", "rms"):
            v.append({"backend": "flite", "id": vid, "name": f"flite {vid} (robotic, testing only)"})
    return v


def pick_voice(settings: dict) -> dict:
    voices = list_voices()
    if not voices:
        raise RuntimeError("no TTS backend found. On macOS `say` is built in; otherwise install piper or espeak-ng.")
    want = settings["tts"].get("voice", "")
    if want:
        for x in voices:
            if x["id"] == want or x["name"] == want:
                return x
    pref = settings["tts"].get("backend", "auto")
    if pref != "auto":
        for x in voices:
            if x["backend"] == pref:
                return x
    if util.have("say"):  # nicer defaults on Mac
        for name in ("Samantha", "Ava", "Evan", "Allison", "Daniel"):
            for x in voices:
                if x["backend"] == "say" and x["id"].startswith(name):
                    return x
    order = {"piper": 0, "say": 1, "espeak": 2, "flite": 3}
    return sorted(voices, key=lambda x: order[x["backend"]])[0]


def _to_wav(src: str, dst: str, rate: float):
    af = f"atempo={rate}," if abs(rate - 1.0) > 0.01 else ""
    util.run(["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-af", af + "aresample=44100,pan=mono|c0=c0,silenceremove=start_periods=1:start_threshold=-50dB",
              "-ar", str(SR), "-ac", "1", dst])


def synth(text: str, out_wav: str, voice: dict, rate: float = 1.0):
    tmp = out_wav + ".raw"
    b = voice["backend"]
    if b == "say":
        util.run(["say", "-v", voice["id"], "-o", tmp + ".aiff", text]); src = tmp + ".aiff"
    elif b == "piper":
        model = next(m for m in _piper_models() if m.stem == voice["id"])
        exe = [util.have("piper")] if util.have("piper") else ["python3", "-m", "piper"]
        util.run(exe + ["--model", str(model), "--output_file", tmp + ".wav"], input=text.encode()); src = tmp + ".wav"
    elif b == "espeak":
        util.run(["espeak-ng", "-v", voice["id"], "-s", "165", "-w", tmp + ".wav", text]); src = tmp + ".wav"
    elif b == "flite":
        safe = text.replace("\\", "").replace("'", "").replace(":", " ").replace("%", " percent")
        util.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"flite=text='{safe}':voice={voice['id']}", tmp + ".wav"]); src = tmp + ".wav"
    else:
        raise ValueError(b)
    _to_wav(src, out_wav, rate)
    for ext in ("", ".aiff", ".wav"):
        pathlib.Path(tmp + ext).unlink(missing_ok=True)


def read_wav(path: str) -> np.ndarray:
    with wave.open(path) as w:
        assert w.getframerate() == SR and w.getnchannels() == 1
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768


def write_wav(path: str, x: np.ndarray, channels=1):
    x = np.clip(x, -1, 1)
    with wave.open(path, "wb") as w:
        w.setnchannels(channels); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes((x * 32767).astype(np.int16).tobytes())


def word_timings(text: str, start: float, dur: float) -> list[dict]:
    """Spread words over [start, start+dur] weighted by length; commas/periods get a small pause weight."""
    words = text.split()
    wts = [max(2, len(re.sub(r"\W", "", w))) + (3 if re.search(r"[,;:]$", w) else 0) + (6 if re.search(r"[.!?]$", w) else 0) for w in words]
    tot = float(sum(wts))
    out, t = [], start
    for w, wt in zip(words, wts):
        d = dur * wt / tot
        out.append({"w": w, "start": round(t, 3), "end": round(t + d, 3)})
        t += d
    return out


def narrate(script: dict, vdir: pathlib.Path, voice: dict, rate: float = 1.0, lead=0.25, gap=0.28, tail=0.9) -> dict:
    """Synthesize every scene; returns timeline {scenes:[{start,end,words}], total, wav}."""
    segs, t, timeline = [], lead, []
    audio = [np.zeros(int(lead * SR), np.float32)]
    for i, sc in enumerate(script["scenes"]):
        p = str(vdir / f"nar_{i:02d}.wav")
        synth(sc["narration"], p, voice, rate)
        x = read_wav(p)
        d = len(x) / SR
        words = word_timings(sc["narration"], t, d)
        end = t + d + gap
        timeline.append({"start": round(t - (lead if i == 0 else gap / 2), 3), "end": round(end - gap / 2 if i < len(script["scenes"]) - 1 else end + tail, 3),
                         "speech_start": round(t, 3), "speech_end": round(t + d, 3), "words": words})
        audio += [x, np.zeros(int(gap * SR), np.float32)]
        t = end
    audio.append(np.zeros(int(tail * SR), np.float32))
    full = np.concatenate(audio)
    write_wav(str(vdir / "narration.wav"), full)
    timeline[0]["start"] = 0.0
    for a, b in zip(timeline, timeline[1:]):   # contiguous scene boundaries
        a["end"] = b["start"]
    return {"scenes": timeline, "total": round(len(full) / SR, 3), "voice": voice}
