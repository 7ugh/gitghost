"""Original, royalty-free background music + SFX generated from scratch with numpy (no samples, no licences to worry about)."""
from __future__ import annotations
import numpy as np
from .tts import SR, read_wav, write_wav

SCALES = {"upbeat": ([0, 4, 7, 12], 118, [0, 5, 3, 4]), "mysterious": ([0, 3, 7, 10], 84, [0, 5, 3, 6]),
          "chill": ([0, 4, 7, 11], 90, [0, 3, 5, 4]), "epic": ([0, 7, 12, 15], 100, [0, 4, 5, 3])}


def _env(n, a=0.02, r=0.3):
    t = np.arange(n) / SR
    return np.minimum(t / a, 1) * np.exp(-t / r)


def music(mood: str, seconds: float, seed: int = 1) -> np.ndarray:
    rng = np.random.default_rng(seed)
    chord, bpm, prog = SCALES.get(mood, SCALES["upbeat"])
    n = int(seconds * SR)
    out = np.zeros(n, np.float32)
    beat = 60 / bpm
    root = 110 * 2 ** (rng.integers(-3, 4) / 12)
    bar = beat * 4
    t_all = np.arange(n) / SR
    for b in range(int(seconds / bar) + 1):
        r = root * 2 ** (prog[b % len(prog)] / 12)
        s, e = int(b * bar * SR), min(n, int((b + 1) * bar * SR))
        if s >= n:
            break
        t = t_all[s:e]
        pad = sum(np.sin(2 * np.pi * r * 2 ** (c / 12) * t + k) * 0.05 for k, c in enumerate(chord[:3]))
        pad += np.sin(2 * np.pi * r / 2 * t) * 0.10  # bass
        fade = np.minimum(1, np.minimum((t - t[0]) / 0.15, (t[-1] - t) / 0.3))
        out[s:e] += (pad * fade).astype(np.float32)
        for k in range(8):  # eighth-note arpeggio
            ns = s + int(k * beat / 2 * SR)
            note = r * 2 * 2 ** (chord[k % len(chord)] / 12)
            seg = int(0.35 * SR)
            if ns + seg > n:
                continue
            tt = np.arange(seg) / SR
            tone = (np.sin(2 * np.pi * note * tt) + 0.3 * np.sin(2 * np.pi * note * 2 * tt)) * _env(seg, 0.005, 0.12) * 0.07
            out[ns:ns + seg] += tone.astype(np.float32)
    # soft pulse on beats for energy (not on 'mysterious'/'chill')
    if mood in ("upbeat", "epic"):
        for k in range(int(seconds / beat)):
            ns, seg = int(k * beat * SR), int(0.12 * SR)
            if ns + seg > n:
                break
            tt = np.arange(seg) / SR
            out[ns:ns + seg] += (np.sin(2 * np.pi * (90 - 250 * tt) * tt) * np.exp(-tt / 0.04) * 0.18).astype(np.float32)
    fade = np.minimum(1, np.minimum(t_all / 1.0, (seconds - t_all) / 1.5))
    return out * fade.astype(np.float32)


def whoosh(dur=0.35, seed=3) -> np.ndarray:
    rng = np.random.default_rng(seed)
    n = int(dur * SR)
    x = rng.standard_normal(n).astype(np.float32)
    k = np.linspace(0, 1, n)
    # moving low-pass via cumulative smoothing: sweep window from large to small
    y = np.empty_like(x)
    acc = 0.0
    for i in range(n):
        a = 0.02 + 0.5 * k[i]
        acc += a * (x[i] - acc)
        y[i] = acc
    return (y * np.sin(np.pi * k) ** 1.5 * 1.8).astype(np.float32)


def pop(dur=0.12) -> np.ndarray:
    n = int(dur * SR); t = np.arange(n) / SR
    return (np.sin(2 * np.pi * (700 + 900 * t / dur) * t) * np.exp(-t / 0.03) * 0.5).astype(np.float32)


def mix(vdir, timeline: dict, mood: str, seed: int = 1) -> str:
    """narration + ducked music + whoosh at each scene cut + pop on hook. Returns path to mix.wav (peak-safe; loudness set at encode)."""
    nar = read_wav(str(vdir / "narration.wav"))
    n = len(nar)
    m = music(mood, n / SR, seed)[:n]
    if len(m) < n:
        m = np.pad(m, (0, n - len(m)))
    # duck music under narration: smoothed amplitude envelope
    win = int(0.08 * SR)
    amp = np.convolve(np.abs(nar), np.ones(win) / win, mode="same")
    amp = np.convolve(amp, np.ones(int(0.25 * SR)) / int(0.25 * SR), mode="same")
    duck = 1.0 - 0.75 * np.clip(amp / 0.08, 0, 1)
    out = nar * 1.0 + m * duck * 1.0
    wsh = whoosh()
    for sc in timeline["scenes"][1:]:
        s = max(0, int((sc["start"] - 0.12) * SR))
        seg = wsh[: max(0, min(len(wsh), n - s))]
        out[s:s + len(seg)] += seg * 0.18
    p = pop()
    out[: len(p)] += p * 0.4
    peak = float(np.max(np.abs(out))) or 1.0
    if peak > 0.95:
        out = out * (0.95 / peak)
    path = str(vdir / "mix.wav")
    write_wav(path, out)
    return path
