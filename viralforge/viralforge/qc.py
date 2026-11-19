"""Automated quality checks on the finished MP4 + project. Each check: pass / warn / fail. Nothing here guarantees performance."""
from __future__ import annotations
import json, re, subprocess
from . import util


def _ngram_overlap(a: str, b: str, n=6) -> float:
    ta, tb = re.findall(r"[a-z0-9']+", a.lower()), re.findall(r"[a-z0-9']+", b.lower())
    ga = {tuple(ta[i:i + n]) for i in range(len(ta) - n + 1)}
    gb = {tuple(tb[i:i + n]) for i in range(len(tb) - n + 1)}
    return len(ga & gb) / len(ga) if ga else 0.0


def run(mp4: str, project: dict) -> dict:
    checks = []
    add = lambda name, status, detail: checks.append({"check": name, "status": status, "detail": detail})
    info = util.ffprobe(mp4)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    a = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    dur = float(info["format"]["duration"])
    num, den = v["r_frame_rate"].split("/")
    fps = float(num) / float(den)
    add("resolution", "pass" if (v["width"], v["height"]) == (1080, 1920) else "fail", f"{v['width']}x{v['height']} (want 1080x1920, 9:16)")
    add("frame rate", "pass" if abs(fps - 30) < .01 or abs(fps - 60) < .01 else "warn", f"{fps:.2f} fps")
    add("codec", "pass" if v["codec_name"] == "h264" and v["pix_fmt"] == "yuv420p" else "warn", f"{v['codec_name']} {v['pix_fmt']}; audio {a['codec_name'] if a else 'MISSING'}")
    add("duration", "pass" if 8 <= dur <= 90 else "warn", f"{dur:.1f}s (Reels sweet spot 8-90s; length follows the content)")
    if not a:
        add("audio", "fail", "no audio stream")
    else:
        out = subprocess.run(["ffmpeg", "-nostats", "-i", mp4, "-af", "ebur128=peak=true", "-f", "null", "-"], capture_output=True, text=True).stderr
        mi = re.findall(r"I:\s+(-?[\d.]+) LUFS", out); mp = re.findall(r"Peak:\s+(-?[\d.]+) dBFS", out)
        lufs = float(mi[-1]) if mi else None; peak = float(mp[-1]) if mp else None
        st = "pass" if lufs is not None and -19 <= lufs <= -13 and (peak is None or peak <= -0.5) else "warn"
        add("loudness", st, f"{lufs} LUFS integrated, true peak {peak} dBFS (target about -16 LUFS, peak <= -1)")
    bd = subprocess.run(["ffmpeg", "-nostats", "-i", mp4, "-vf", "blackdetect=d=0.5:pic_th=0.98", "-an", "-f", "null", "-"], capture_output=True, text=True).stderr
    add("black frames", "pass" if "black_start" not in bd else "warn", "none longer than 0.5s" if "black_start" not in bd else "black segment detected")
    fz = subprocess.run(["ffmpeg", "-nostats", "-i", mp4, "-vf", "freezedetect=n=-60dB:d=2.5", "-an", "-f", "null", "-"], capture_output=True, text=True).stderr
    add("frozen video", "pass" if "freeze_start" not in fz else "warn", "motion throughout" if "freeze_start" not in fz else "a frozen segment of >2.5s was detected")

    caps = project.get("captions", [])
    if caps:
        short = [c for c in caps if c["end"] - c["start"] < 0.3]
        cps = max(sum(len(w["w"]) for w in c["words"]) / max(0.05, c["end"] - c["start"]) for c in caps)
        add("caption readability", "pass" if not short and cps <= 22 and project.get("min_caption_font", 99) >= 56 else "warn",
            f"{len(caps)} chunks, min font {project.get('min_caption_font')}px, fastest {cps:.0f} chars/s, {len(short)} shown <0.3s")
        add("caption sync", "pass" if caps[0]["start"] < 0.6 else "warn", f"first caption at {caps[0]['start']:.2f}s")
    hook_words = len(project["script"]["scenes"][0]["narration"].split())
    add("opening hook", "pass" if hook_words <= 16 else "warn", f"{hook_words} words before the first beat; speech starts at {project['timeline']['scenes'][0]['speech_start']:.2f}s")

    fc = project.get("factcheck", {})
    if fc.get("applicable") is False:
        add("fact verification", "pass", fc.get("summary", "n/a (fiction)"))
    elif fc:
        c = fc.get("counts", {})
        st = "pass" if fc.get("ok") and c.get("single_source", 0) == 0 else ("warn" if c.get("unsupported", 0) == 0 else "fail")
        add("fact verification", st, fc["summary"] + (" - single-source facts: verify before posting" if c.get("single_source") else ""))
    else:
        add("fact verification", "warn", "not run")

    src_text = " ".join(f["text"] for f in project.get("notes", {}).get("facts", []))
    nar = " ".join(s["narration"] for s in project["script"]["scenes"])
    ov = _ngram_overlap(nar, src_text) if src_text else 0.0
    own = all(src.get("trust") == "user" for src in project.get("notes", {}).get("sources", [])) and bool(project.get("notes", {}).get("sources"))
    bad = "warn" if own else "fail"     # your own notes may be reused word for word; third-party text may not
    add("originality vs sources", "pass" if ov < 0.2 else ("warn" if ov < 0.5 else bad),
        f"{ov*100:.0f}% of 6-word phrases also appear verbatim in the source notes" + (" (your own notes: OK to reuse, but a local LLM rewrites more naturally)" if own and ov >= 0.2 else " (copying risk; lower is better)"))
    unlicensed = [a for a in project.get("assets", []) if not a.get("license")]
    add("asset licences", "pass" if not unlicensed else "fail",
        f"{len(project.get('assets', []))} external asset(s); procedural visuals + generated music otherwise" if not unlicensed else f"{len(unlicensed)} external asset(s) lack a licence note")
    d = project.get("description", {})
    add("description", "pass" if d and d["length"] <= 2200 and len(d["hashtags"]) <= 30 else "fail", f"{d.get('length')} chars, {len(d.get('hashtags', []))} hashtags (limits: 2200 / 30)")
    status = "fail" if any(c["status"] == "fail" for c in checks) else "warn" if any(c["status"] == "warn" for c in checks) else "pass"
    return {"overall": status, "checks": checks}
