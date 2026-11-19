"""Small shared helpers: HTTP, subprocess, text."""
from __future__ import annotations
import json, re, subprocess, time, urllib.request, urllib.error, shutil

UA = "ViralForge/0.1 (personal research tool; respects robots/ToS; low volume)"
STOPWORDS = set("""a an the of to in on for and or but is are was were be been being it its this that these those with as at by from
about into over after before up down out off than then so such not no yes you your yours we our they their he she his her i me my do does did
done have has had how what why when where who whom which will would can could should may might just very more most new news says say said
vs via get gets got one two three""".split())


def now() -> float:
    return time.time()


def fetch(url: str, headers: dict | None = None, timeout: float = 15, max_bytes: int = 3_000_000) -> bytes:
    h = {"User-Agent": UA, "Accept": "*/*"}
    h.update(headers or {})
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(max_bytes)


def fetch_json(url: str, **kw):
    return json.loads(fetch(url, **kw).decode("utf-8", "replace"))


def run(cmd: list[str], check=True, timeout=None, input: bytes | None = None) -> subprocess.CompletedProcess:
    p = subprocess.run(cmd, capture_output=True, timeout=timeout, input=input)
    if check and p.returncode != 0:
        raise RuntimeError(f"command failed ({p.returncode}): {' '.join(cmd[:4])}...\n{p.stderr.decode('utf-8','replace')[-1500:]}")
    return p


def have(binary: str) -> str | None:
    return shutil.which(binary)


def slugify(s: str, n=48) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s[:n].strip("-") or "video"


def stem(w: str) -> str:
    if len(w) < 5:
        return w
    if w.endswith("ies"):
        return w[:-3] + "y"
    if w.endswith(("uses", "ses", "xes", "ches", "shes", "zes")):
        return w[:-2]
    if w.endswith("s") and not w.endswith(("ss", "us", "is")):
        return w[:-1]
    return w


def tokens(s: str) -> list[str]:
    return [stem(w) for w in re.findall(r"[a-z0-9']+", s.lower()) if w not in STOPWORDS and len(w) > 1]


def sentences(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text.strip())
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'])", text)
    return [p.strip() for p in parts if len(p.strip()) > 2]


def ffprobe(path: str) -> dict:
    p = run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)])
    return json.loads(p.stdout)
