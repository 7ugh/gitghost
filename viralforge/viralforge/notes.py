"""Gather source notes for a topic: [{id,text,source}] + sources [{id,title,url,license,trust}].

Live: Wikipedia (CC BY-SA, attribution required) + the trend signals' own summaries (low trust).
Offline/own research: pass a notes JSON file (--notes) that you wrote or verified.
"""
from __future__ import annotations
import json, re, urllib.parse
from . import util


def from_file(path: str) -> dict:
    d = json.load(open(path))
    d.setdefault("sources", []); d.setdefault("facts", [])
    for s in d["sources"]:
        s.setdefault("trust", "user")
    return d


def wikipedia(topic: str) -> tuple[list[dict], list[dict]]:
    q = urllib.parse.quote(re.sub(r"^TIL( that)?\s+", "", topic, flags=re.I))
    hits = util.fetch_json(f"https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch={q}&srlimit=2&format=json")["query"]["search"]
    facts, sources = [], []
    for n, h in enumerate(hits, 1):
        t = urllib.parse.quote(h["title"].replace(" ", "_"))
        j = util.fetch_json(f"https://en.wikipedia.org/api/rest_v1/page/summary/{t}")
        sid = f"W{n}"
        sources.append({"id": sid, "title": f"Wikipedia: {j['title']}", "url": j["content_urls"]["desktop"]["page"], "license": "CC BY-SA 4.0", "trust": "encyclopedia"})
        for k, s in enumerate(util.sentences(j.get("extract", ""))[:8], 1):
            facts.append({"id": f"{sid}.{k}", "text": s, "source": sid})
    return facts, sources


def gather(topic: str, signals: list[dict] | None = None, notes_file: str | None = None) -> dict:
    if notes_file:
        d = from_file(notes_file)
        d.setdefault("topic", topic)
        d["mode"] = "user-notes"
        return d
    facts, sources, warnings = [], [], []
    try:
        facts, sources = wikipedia(topic)
    except Exception as e:
        warnings.append(f"Wikipedia unreachable ({type(e).__name__}); facts cannot be verified from an encyclopedia")
    for n, s in enumerate(signals or [], 1):
        if s.get("summary"):
            sid = f"T{n}"
            sources.append({"id": sid, "title": s["title"][:80], "url": s.get("url", ""), "license": "n/a (cited, not copied)", "trust": "trend-signal"})
            for k, sent in enumerate(util.sentences(s["summary"])[:3], 1):
                facts.append({"id": f"{sid}.{k}", "text": sent, "source": sid})
    return {"topic": topic, "sources": sources, "facts": facts, "mode": "live" if facts else "empty", "warnings": warnings}
