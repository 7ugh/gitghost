"""Frame renderer (Pillow + numpy) piped into FFmpeg. 1080x1920 @ 30fps, H.264/AAC.

Visual styles: procedural animated motifs, or your own authorized images/clips per scene (scene.image / scene.clip).
No external assets are downloaded or reused.
"""
from __future__ import annotations
import colorsys, json, math, pathlib, re, subprocess, tempfile, shutil
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from . import util

W, H = 1080, 1920
FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial Rounded Bold.ttf", "/System/Library/Fonts/Supplemental/Arial Black.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/Library/Fonts/Arial Bold.ttf",
    "/usr/share/fonts/opentype/inter/Inter-ExtraBold.otf", "/usr/share/fonts/opentype/inter/Inter-Bold.otf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]
PALETTES = [  # top, bottom, accent
    ((18, 24, 64), (88, 28, 135), (255, 214, 10)), ((7, 40, 56), (14, 116, 144), (255, 107, 107)),
    ((46, 16, 60), (190, 40, 90), (255, 230, 109)), ((12, 44, 28), (22, 130, 90), (255, 183, 3)),
    ((26, 26, 46), (22, 33, 62), (0, 229, 255)), ((60, 20, 10), (200, 80, 30), (255, 244, 180)),
]
MOOD_PALETTE = {"upbeat": [0, 2, 5], "mysterious": [4, 0, 1], "chill": [1, 3, 4], "epic": [5, 2, 0]}
_fonts = {}


def font(size: int) -> ImageFont.FreeTypeFont:
    size = int(size)
    if size not in _fonts:
        for p in FONT_CANDIDATES:
            if pathlib.Path(p).exists():
                _fonts[size] = ImageFont.truetype(p, size)
                break
        else:
            _fonts[size] = ImageFont.load_default(size)
    return _fonts[size]


def ease_out_back(x):
    x = min(max(x, 0), 1); c1 = 1.70158; c3 = c1 + 1
    return 1 + c3 * (x - 1) ** 3 + c1 * (x - 1) ** 2


def ease(x):
    x = min(max(x, 0), 1)
    return 1 - (1 - x) ** 3


# ------------------------------------------------------------------ captions
def build_captions(timeline: dict, max_words=3, max_chars=22) -> list[dict]:
    chunks = []
    for sc in timeline["scenes"]:
        cur = []
        def flush():
            if cur:
                chunks.append({"start": cur[0]["start"], "end": cur[-1]["end"], "words": [dict(w) for w in cur]})
                cur.clear()
        for w in sc["words"]:
            if cur and (len(cur) >= max_words or len(" ".join(x["w"] for x in cur + [w])) > max_chars):
                flush()
            cur.append(w)
            if re.search(r"[.!?,;:]$", w["w"]):
                flush()
        flush()
    for a, b in zip(chunks, chunks[1:]):   # hold each chunk until next begins (max 0.25s gap)
        a["end"] = round(min(b["start"], a["end"] + 0.25), 3)
    return chunks


def retime_chunk(chunk: dict, new_text: str) -> dict:
    words = new_text.split()
    if not words:
        return chunk
    span = chunk["end"] - chunk["start"]
    wts = [max(2, len(w)) for w in words]
    t, out = chunk["start"], []
    for w, wt in zip(words, wts):
        d = span * wt / sum(wts)
        out.append({"w": w, "start": round(t, 3), "end": round(t + d, 3)}); t += d
    return {**chunk, "words": out}


def to_srt(caps: list[dict]) -> str:
    def ts(x):
        h, r = divmod(x, 3600); m, s = divmod(r, 60)
        return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{int(round((s % 1) * 1000)):03d}"
    return "\n".join(f"{i}\n{ts(c['start'])} --> {ts(c['end'])}\n{' '.join(w['w'] for w in c['words'])}\n" for i, c in enumerate(caps, 1))


# ------------------------------------------------------------------ motifs (drawn 2x supersampled on 640x640 -> 1280)
def _poly_heart(cx, cy, s, n=60):
    pts = []
    for i in range(n):
        t = 2 * math.pi * i / n
        x = 16 * math.sin(t) ** 3
        y = -(13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t))
        pts.append((cx + x * s / 17, cy + y * s / 17))
    return pts


def _star(cx, cy, r1, r2, rot, n=5):
    return [(cx + (r1 if i % 2 == 0 else r2) * math.cos(rot + i * math.pi / n), cy + (r1 if i % 2 == 0 else r2) * math.sin(rot + i * math.pi / n)) for i in range(2 * n)]


def glow(img, fn, radius=14):
    """draw shape via fn(draw) on a copy, blur it, and put it under the crisp version."""
    g = Image.new("RGBA", img.size, (0, 0, 0, 0)); fn(ImageDraw.Draw(g))
    small = g.reduce(4).filter(ImageFilter.GaussianBlur(max(1, radius / 4)))   # blur at 1/4 res: ~16x cheaper
    img.alpha_composite(small.resize(img.size, Image.BILINEAR))
    fn(ImageDraw.Draw(img))


def hero(motif: str, u: float, p: float, accent, stat: str | None, S=1280) -> Image.Image:
    """u = seconds since scene start, p = scene progress 0..1. Returns RGBA SxS."""
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0)); c = S // 2
    gl = Image.new("RGBA", (S, S), (0, 0, 0, 0)); gd = ImageDraw.Draw(gl)

    def glow(_img, fn, radius=14):          # all glows share one blur pass (draw twice: halo layer + crisp layer)
        fn(gd); fn(ImageDraw.Draw(im))
    col = accent + (255,); white = (255, 255, 255, 255)
    n = 3
    try:
        n = max(1, min(9, int(re.sub(r"\D", "", stat or "") or 3)))
    except ValueError:
        pass
    if motif == "heart":
        pos = [(c, c - 150), (c - 230, c + 150), (c + 230, c + 150)] if n == 3 else [(c + (i - (n - 1) / 2) * min(300, 900 / n), c) for i in range(min(n, 5))]
        for i, (x, y) in enumerate(pos):
            beat = 1 + 0.12 * max(0, math.sin(u * 7 + i * 1.3)) ** 2
            s = 330 * beat * ease_out_back(u * 2 - i * 0.2)
            glow(im, lambda d, x=x, y=y, s=s: d.polygon(_poly_heart(x, y, s), fill=(255, 70, 100, 255)), 24)
    elif motif == "orbs":
        k = max(3, n)
        for i in range(k):
            a = u * 1.1 + i * 2 * math.pi / k
            r = 330 + 30 * math.sin(u * 2 + i)
            x, y = c + r * math.cos(a), c + r * math.sin(a)
            rad = (70 + 18 * math.sin(u * 3 + i)) * ease_out_back(u * 2 - i * 0.08)
            glow(im, lambda d, x=x, y=y, rad=rad, i=i: d.ellipse((x - rad, y - rad, x + rad, y + rad), fill=col if i % 2 == 0 else white), 20)
        glow(im, lambda d: d.ellipse((c - 90, c - 90, c + 90, c + 90), fill=col), 30)
    elif motif == "star":
        s = ease_out_back(u * 1.6)
        glow(im, lambda d: d.polygon(_star(c, c, 420 * s, 180 * s, u * 0.9), fill=col), 26)
    elif motif == "bars":
        vals = [0.35, 0.6, 0.45, 0.85, 0.7]
        for i, v in enumerate(vals):
            h = 760 * v * ease(u * 1.4 - i * 0.12)
            x0 = c - 460 + i * 190
            glow(im, lambda d, x0=x0, h=h, i=i: d.rounded_rectangle((x0, c + 380 - h, x0 + 150, c + 380), 30, fill=col if i == 3 else white), 14)
    elif motif == "ring":
        for i in range(4):
            r = ((u * 0.45 + i * 0.25) % 1.0) * 560 + 40
            a = int(255 * (1 - (r - 40) / 560))
            ImageDraw.Draw(im).ellipse((c - r, c - r, c + r, c + r), outline=accent + (a,), width=26)
        glow(im, lambda d: d.ellipse((c - 70, c - 70, c + 70, c + 70), fill=col), 24)
    elif motif == "spark":
        d = ImageDraw.Draw(im)
        for i in range(14):
            a = i * 2 * math.pi / 14 + u * 0.3
            r0 = 120 + 40 * math.sin(u * 4 + i); r1 = (360 + 120 * (i % 3)) * ease_out_back(u * 1.8)
            d.line((c + r0 * math.cos(a), c + r0 * math.sin(a), c + r1 * math.cos(a), c + r1 * math.sin(a)), fill=col if i % 2 else white, width=24)
        glow(im, lambda d: d.ellipse((c - 100, c - 100, c + 100, c + 100), fill=col), 30)
    elif motif == "question":
        f = font(1000); wob = math.sin(u * 3) * 6
        t = Image.new("RGBA", (S, S), (0, 0, 0, 0)); ImageDraw.Draw(t).text((c, c), "?", font=f, fill=col, anchor="mm", stroke_width=14, stroke_fill=(20, 20, 40, 255))
        im.alpha_composite(t.resize((S // 2, S // 2), Image.LANCZOS).rotate(wob, resample=Image.BICUBIC).resize((S, S), Image.BILINEAR))
    elif motif == "bolt":
        pts = [(c + 60, c - 450), (c - 220, c + 40), (c - 20, c + 40), (c - 90, c + 450), (c + 230, c - 90), (c + 30, c - 90)]
        s = ease_out_back(u * 2)
        pts = [(c + (x - c) * s, c + (y - c) * s) for x, y in pts]
        glow(im, lambda d: d.polygon(pts, fill=col), 30)
    elif motif == "moon":
        glow(im, lambda d: d.ellipse((c - 340, c - 340, c + 340, c + 340), fill=(240, 240, 255, 255)), 36)
        sh = 220 * ease(u * 0.6)
        ImageDraw.Draw(im).ellipse((c - 340 + sh, c - 340 - 40, c + 340 + sh, c + 340 - 40), fill=(0, 0, 0, 0))
        mask = Image.new("L", (S, S), 0); ImageDraw.Draw(mask).ellipse((c - 340 + sh, c - 340 - 40, c + 340 + sh, c + 340 - 40), fill=255)
        im.paste((0, 0, 0, 0), (0, 0), mask)
    elif motif == "wave":
        d = ImageDraw.Draw(im)
        for k in range(4):
            pts = [(x, c + (k - 1.5) * 110 + 90 * math.sin(x / 120 + u * 3 + k)) for x in range(80, S - 80, 12)]
            d.line(pts, fill=col if k % 2 else white, width=26, joint="curve")
    if motif == "stat" or (stat and motif not in ("heart", "orbs")):
        d = ImageDraw.Draw(im)
        glow(im, lambda dd: dd.ellipse((c - 360, c - 360, c + 360, c + 360), outline=col, width=26), 22)
        target = re.sub(r"[^\d.]", "", stat or "")
        shown = stat or ""
        if target and "." not in target and len(target) <= 7:
            shown = re.sub(r"\d+", lambda m: f"{int(int(m.group()) * ease(u * 1.3)):,}", stat)
        fs = 420 if len(shown) <= 3 else 300 if len(shown) <= 5 else 200
        d.text((c, c), shown, font=font(fs), fill=white, anchor="mm", stroke_width=12, stroke_fill=(20, 20, 40, 255))
    halo = gl.reduce(4).filter(ImageFilter.GaussianBlur(5)).resize((S, S), Image.BILINEAR)
    halo.alpha_composite(im)
    return halo.reduce(2)


# ------------------------------------------------------------------ renderer
class Renderer:
    def __init__(self, script: dict, timeline: dict, captions: list[dict], vdir: pathlib.Path, fps=30, seed=7):
        self.script, self.tl, self.caps, self.vdir, self.fps = script, timeline, captions, vdir, fps
        pal = MOOD_PALETTE.get(script.get("mood", "upbeat"), [0, 1, 2])
        self.pals = [PALETTES[pal[i % len(pal)]] for i in range(len(script["scenes"]))]
        self.total = timeline["total"]
        rng = np.random.default_rng(seed)
        self.blobs = [rng.random((14, 5)) for _ in script["scenes"]]  # x,y,size,speed,phase
        self.grad = [self._gradient(*p[:2]) for p in self.pals]
        self.media: dict[int, object] = {}
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="vf_clip_"))
        self.min_caption_font = 999
        self.caption_fonts = [self._fit_caption(c) for c in captions]
        for i, sc in enumerate(script["scenes"]):
            if sc.get("image") and pathlib.Path(sc["image"]).exists():
                self.media[i] = Image.open(sc["image"]).convert("RGB")
            elif sc.get("clip") and pathlib.Path(sc["clip"]).exists():
                self.media[i] = self._extract_clip(i, sc["clip"], timeline["scenes"][i])

    def close(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    @staticmethod
    def _gradient(top, bot, w=360, h=640):
        t = np.linspace(0, 1, h)[:, None, None]
        g = np.array(top)[None, None, :] * (1 - t) + np.array(bot)[None, None, :] * t
        return np.repeat(g, w, axis=1).astype(np.uint8)

    def _extract_clip(self, i, clip, tl):
        d = self.tmp / f"c{i}"; d.mkdir()
        dur = tl["end"] - tl["start"]
        util.run(["ffmpeg", "-y", "-loglevel", "error", "-i", clip, "-t", f"{dur:.2f}", "-vf",
                  f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={self.fps}", "-q:v", "4", str(d / "%05d.jpg")])
        return sorted(d.glob("*.jpg"))

    def _fit_caption(self, chunk):
        text = " ".join(w["w"] for w in chunk["words"]).upper()
        size = 104
        while size > 56 and font(size).getlength(text) > W - 140:
            size -= 4
        self.min_caption_font = min(self.min_caption_font, size)
        return size

    # ---- layers
    def _scene_index(self, t):
        for i, s in enumerate(self.tl["scenes"]):
            if t < s["end"]:
                return i
        return len(self.tl["scenes"]) - 1

    def _background(self, i, u, dur) -> Image.Image:
        sc = self.script["scenes"][i]
        if i in self.media:
            m = self.media[i]
            if isinstance(m, list):
                fr = m[min(int(u * self.fps), len(m) - 1)]
                bg = Image.open(fr).convert("RGB")
            else:
                z = 1.0 + 0.12 * (u / max(dur, 0.1))
                s = max(W / m.width, H / m.height) * z
                r = m.resize((int(m.width * s) + 1, int(m.height * s) + 1), Image.BILINEAR)
                x, y = (r.width - W) // 2, (r.height - H) // 2
                bg = r.crop((x, y, x + W, y + H))
            scrim = Image.new("RGBA", (W, H), (0, 0, 0, 90))
            return Image.alpha_composite(bg.convert("RGBA"), scrim).convert("RGB")
        small = Image.fromarray(self.grad[i])
        ov = Image.new("RGBA", small.size, (0, 0, 0, 0)); d = ImageDraw.Draw(ov)
        acc = self.pals[i][2]
        for x, y, s, sp, ph in self.blobs[i]:
            cx = (x + 0.08 * math.sin(u * sp * 0.8 + ph * 6)) * 360; cy = (y + 0.06 * math.cos(u * sp * 0.6 + ph * 6)) * 640
            r = 14 + s * 34
            d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=acc + (46,))
        small = Image.alpha_composite(small.convert("RGBA"), ov.filter(ImageFilter.GaussianBlur(5))).convert("RGB")
        z = 1.0 + 0.07 * (u / max(dur, 0.1))
        cw, ch = 360 / z, 640 / z
        x0, y0 = (360 - cw) / 2, (640 - ch) / 2
        return small.resize((W, H), Image.BILINEAR, box=(x0, y0, x0 + cw, y0 + ch))

    def _text_block(self, overlay, text, cy, size, fill, alpha=1.0, scale=1.0, max_w=940, stroke=10):
        words = text.split(); lines, cur = [], ""
        while size > 48 and max(font(size).getlength(w) for w in words) > max_w:   # longest word must fit
            size -= 6
        f = font(size)
        for w in words:
            tst = (cur + " " + w).strip()
            if f.getlength(tst) > max_w and cur:
                lines.append(cur); cur = w
            else:
                cur = tst
        lines.append(cur)
        while len(lines) > 3 and size > 60:
            size -= 8; f = font(size)
            lines = [" ".join(words)]  # re-wrap crude
            cur, lines = "", []
            for w in words:
                tst = (cur + " " + w).strip()
                if f.getlength(tst) > max_w and cur:
                    lines.append(cur); cur = w
                else:
                    cur = tst
            lines.append(cur)
        lh = int(size * 1.12)
        bw, bh = max_w + 2 * stroke + 20, lh * len(lines) + 2 * stroke + 20
        layer = Image.new("RGBA", (bw, bh), (0, 0, 0, 0)); d = ImageDraw.Draw(layer)
        for k, ln in enumerate(lines):
            d.text((bw // 2, 10 + stroke + lh * k + lh // 2), ln, font=f, fill=fill, anchor="mm", stroke_width=stroke, stroke_fill=(10, 10, 30, 255))
        if scale != 1.0:
            layer = layer.resize((max(1, int(bw * scale)), max(1, int(bh * scale))), Image.BILINEAR)
        if alpha < 1.0:
            layer.putalpha(layer.getchannel("A").point(lambda v: int(v * alpha)))
        overlay.paste(layer, (int(W / 2 - layer.width / 2), int(cy - layer.height / 2)), layer)

    def _scene_frame(self, i, t, with_caption=True, title=None) -> Image.Image:
        s = self.tl["scenes"][i]; sc = self.script["scenes"][i]
        dur = s["end"] - s["start"]; u = max(0.0, t - s["start"])
        ov = self._background(i, u, dur)          # RGB canvas; layers are pasted straight onto it (no full-size RGBA temp)
        accent = self.pals[i][2]
        bar = Image.new("RGBA", (W - 120, 10), (0, 0, 0, 0)); bd = ImageDraw.Draw(bar)
        bd.rounded_rectangle((0, 0, W - 121, 9), 5, fill=(255, 255, 255, 60))
        bd.rounded_rectangle((0, 0, max(10, (W - 120) * min(1, t / self.total)), 9), 5, fill=accent + (255,))
        ov.paste(bar, (60, 170), bar)
        # hero motif
        if i not in self.media and title is None:
            hs = ease_out_back(u * 2.2) if u < 0.5 else 1.0
            h = hero(sc.get("motif", "orbs"), u, u / max(dur, .1), accent, sc.get("stat"))
            k = max(0.05, hs) * 1.0
            h = h.resize((int(h.width * k), int(h.height * k)), Image.BILINEAR) if abs(k - 1) > 0.01 else h
            ov.paste(h, (int(W / 2 - h.width / 2), int(820 - h.height / 2)), h)
        # headline text
        label = title if title is not None else sc.get("text", "")
        if label:
            a = ease(u * 5)
            self._text_block(ov, label, 400 if title is None else 880, 118 if title is None else 170, (255, 255, 255, 255), alpha=a,
                             scale=0.75 + 0.25 * ease_out_back(u * 3.2))
        # captions
        if with_caption:
            for ci, c in enumerate(self.caps):
                if c["start"] - 0.02 <= t < c["end"]:
                    self._caption(ov, c, self.caption_fonts[ci], t, accent)
                    break
        return ov

    def _caption(self, ov, c, size, t, accent):
        f = font(size); d = ImageDraw.Draw(ov)
        words = [w["w"].upper() for w in c["words"]]
        space = f.getlength(" ")
        widths = [f.getlength(w) for w in words]
        total = sum(widths) + space * (len(words) - 1)
        pop = 0.9 + 0.1 * ease_out_back((t - c["start"]) * 7)
        layer = Image.new("RGBA", (int(total) + 60, int(size * 1.6)), (0, 0, 0, 0)); ld = ImageDraw.Draw(layer)
        x = 30
        for w, wd, wt in zip(words, widths, c["words"]):
            active = wt["start"] <= t < wt["end"]
            ld.text((x, layer.height // 2), w, font=f, fill=accent + (255,) if active else (255, 255, 255, 255), anchor="lm", stroke_width=11, stroke_fill=(10, 10, 30, 255))
            x += wd + space
        if abs(pop - 1) > 0.005:
            layer = layer.resize((int(layer.width * pop), int(layer.height * pop)), Image.BILINEAR)
        ov.paste(layer, (int(W / 2 - layer.width / 2), int(1230 - layer.height / 2)), layer)

    def frame(self, t: float, with_caption=True, title=None) -> np.ndarray:
        t = min(max(t, 0), self.total - 1e-3)
        i = self._scene_index(t)
        f = self._scene_frame(i, t, with_caption, title)
        xf = 0.22
        if i + 1 < len(self.tl["scenes"]) and t > self.tl["scenes"][i]["end"] - xf and title is None:
            a = (t - (self.tl["scenes"][i]["end"] - xf)) / xf
            g = self._scene_frame(i + 1, t, with_caption, title)
            f = Image.blend(f, g, ease(a))
        return np.asarray(f)

    def cover(self, title: str, t: float) -> Image.Image:
        i = self._scene_index(t)
        im = self._scene_frame(i, t, with_caption=False, title=title)
        shade = Image.new("RGBA", (W, H), (0, 0, 0, 0)); d = ImageDraw.Draw(shade)
        for y in range(H):
            d.line((0, y, W, y), fill=(0, 0, 0, int(150 * (abs(y - H / 2) / (H / 2)) ** 2)))
        return Image.alpha_composite(im.convert("RGBA"), shade).convert("RGB")


_R: Renderer | None = None


def _work(k):
    return _R.frame(k / _R.fps).tobytes()


def encode(renderer: Renderer, audio_wav: str, out_mp4: str, fps=30, crf=18, preset="medium", encoder="libx264", progress=None, workers=None):
    import multiprocessing as mp, os
    global _R
    renderer.fps = fps
    n = int(math.ceil(renderer.total * fps))
    venc = ["-c:v", "libx264", "-preset", preset, "-crf", str(crf)] if encoder == "libx264" else ["-c:v", encoder, "-b:v", "12M"]
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(fps), "-i", "-",
           "-i", audio_wav, *venc, "-profile:v", "high", "-pix_fmt", "yuv420p", "-r", str(fps),
           "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-t", f"{renderer.total:.3f}",
           "-movflags", "+faststart", out_mp4]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    workers = workers or max(1, (os.cpu_count() or 2) - 1)
    _R = renderer
    try:
        if workers > 1 and "fork" in mp.get_all_start_methods():
            with mp.get_context("fork").Pool(workers) as pool:
                for k, buf in enumerate(pool.imap(_work, range(n), chunksize=6)):
                    p.stdin.write(buf)
                    if progress and k % 15 == 0:
                        progress(k / n)
        else:
            for k in range(n):
                p.stdin.write(renderer.frame(k / fps).tobytes())
                if progress and k % 15 == 0:
                    progress(k / n)
        p.stdin.close()
        err = p.stderr.read().decode()
        if p.wait() != 0:
            raise RuntimeError("ffmpeg encode failed: " + err[-1200:])
    finally:
        if p.poll() is None:
            p.kill()
        _R = None
