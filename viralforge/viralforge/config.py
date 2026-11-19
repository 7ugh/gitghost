"""Settings + filesystem layout. Everything lives under $VIRALFORGE_HOME (default ./data)."""
from __future__ import annotations
import json, os, pathlib, copy, secrets

HOME = pathlib.Path(os.environ.get("VIRALFORGE_HOME", pathlib.Path(__file__).resolve().parent.parent / "data"))

CATEGORIES = ["comedy", "animated stories", "mysteries", "facts", "what-if", "technology", "gaming",
              "roblox", "challenges", "internet trends", "storytelling", "educational"]

DEFAULTS = {
    "interests": {c: 1.0 for c in CATEGORIES},          # 0..1 weight per category (your niche preference)
    "daily": {"enabled": False, "time": "09:00", "styles": "auto"},
    "llm": {"backend": "auto", "ollama_url": "http://127.0.0.1:11434", "model": ""},
    "tts": {"backend": "auto", "voice": "", "rate": 1.0},
    "video": {"fps": 30, "crf": 18, "preset": "medium", "encoder": "libx264", "disclose_ai": True},
    "sources": {
        "reddit": {"enabled": True, "subreddits": ["todayilearned", "interestingasfuck", "Showerthoughts", "technology",
                    "gaming", "roblox", "space", "science", "UnresolvedMysteries", "funny", "mildlyinteresting", "NoStupidQuestions"]},
        "hackernews": {"enabled": True},
        "google_trends": {"enabled": True, "geo": "US"},
        "wikipedia": {"enabled": True},
        "news_rss": {"enabled": True, "feeds": [
            "http://feeds.bbci.co.uk/news/technology/rss.xml",
            "http://feeds.bbci.co.uk/news/science_and_environment/rss.xml",
            "http://feeds.bbci.co.uk/news/entertainment_and_arts/rss.xml",
            "https://feeds.arstechnica.com/arstechnica/index"]},
        "youtube": {"enabled": True, "api_key_env": "YOUTUBE_API_KEY", "region": "US", "channel_ids": [], "shorts_queries": []},
    },
    "instagram": {
        "user_id": "", "token_env": "IG_ACCESS_TOKEN", "graph_base": "https://graph.instagram.com/v23.0",
        "rupload_base": "https://rupload.facebook.com/ig-api-upload/v23.0",
        "mode": "resumable",            # resumable | video_url
        "public_base_url": "",          # only for mode=video_url (e.g. a tunnel to this server)
    },
    "posting_hours_local": [12, 18, 20],   # defaults until your own analytics say otherwise
    "server": {"host": "127.0.0.1", "port": 8765, "token": ""},
}


def merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = merge(out[k], v)
        else:
            out[k] = v
    return out


def ensure_dirs():
    for d in ("videos", "tmp", "piper"):
        (HOME / d).mkdir(parents=True, exist_ok=True)


def load() -> dict:
    ensure_dirs()
    load_env()
    p = HOME / "settings.json"
    cur = json.loads(p.read_text()) if p.exists() else {}
    s = merge(DEFAULTS, cur)
    if not s["server"]["token"]:
        s["server"]["token"] = secrets.token_urlsafe(16)
        save(s)
    return s


def save(s: dict):
    ensure_dirs()
    p = HOME / "settings.json"
    p.write_text(json.dumps(s, indent=2))
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


def video_dir(vid: str) -> pathlib.Path:
    d = HOME / "videos" / vid
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_env():
    """Read $VIRALFORGE_HOME/.env (KEY=VALUE lines, chmod 600) into os.environ. Tokens never go into settings.json."""
    p = HOME / ".env"
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip("'\""))
