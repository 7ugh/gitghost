"""Cluster signals into topics and rank them with a transparent, heuristic score.

The score is a *prioritisation aid*, not a prediction. Every component is shown, every weight is public,
and nothing here can promise views.
"""
from __future__ import annotations
import json, math, re, time
from collections import defaultdict
from . import config, db, research, util

WEIGHTS = {  # sum to 1.0
    "relevance": 0.15, "interest": 0.25, "competition": 0.10, "shareability": 0.15,
    "comments": 0.10, "story": 0.10, "hook": 0.10, "retention": 0.05,
}
COMPONENT_HELP = {
    "relevance": "How well the topic fits the categories you said you care about.",
    "interest": "Recent engagement vs. other items from the same source (percentile), with a 24h half-life and a bonus when several independent sources show it.",
    "competition": "PROXY: 100 minus how many video/news items in our sample already cover this topic. Fewer = more room. Cannot see TikTok/Instagram saturation.",
    "shareability": "Heuristic: surprise, numbers, universal curiosity, relatable emotion words.",
    "comments": "Heuristic: debatable, question-shaped, or opinion-inviting topics.",
    "story": "Heuristic: narrative ingredients (a person/place/mystery, a turn, a reveal).",
    "hook": "Heuristic: can we open with a number, contrast, question or stakes that the video can truthfully pay off.",
    "retention": "Heuristic: topic supports a reveal structure that fits a short video.",
}
SENSITIVE = re.compile(r"\b(shoot(ing|er)?|murder\w*|kill(ed|ing|s)?|dead|death|died|suicide|rape\w*|sexual\w*|terror\w*|massacre|"
                       r"war crimes?|abuse[sd]?|overdose|stabb\w*|bomb(ing)?|hostage|nazi|genocide|porn\w*|nsfw|self-harm|trump|biden|election)\b", re.I)
FORMAT_BY_CAT = {"facts": "explainer", "educational": "explainer", "technology": "explainer", "internet trends": "explainer",
                 "what-if": "what_if", "mysteries": "story", "storytelling": "story", "animated stories": "story",
                 "comedy": "comedy", "challenges": "story", "gaming": "explainer", "roblox": "explainer"}
SURPRISE = ["secret", "hidden", "never", "actually", "turns out", "impossible", "weird", "strange", "why", "how", "vanish", "mystery", "shock",
            "first", "only", "biggest", "smallest", "ancient", "discovered", "unexpected", "blue", "three", "million", "billion"]
DEBATE = ["vs", "better", "worst", "best", "should", "would you", "is it", "why do", "ban", "fair", "real", "fake", "overrated", "what if"]
NARR = ["vanish", "lost", "found", "built", "escape", "disappear", "last", "discovered", "mystery", "story", "journey", "survive", "secret", "keepers", "players"]


def _pct_rank(values):
    """percentile (0..1) of each value among values."""
    srt = sorted(values)
    n = len(srt)
    return [sum(1 for v in srt if v <= x) / n for x in values]


def _jaccard(a, b):
    return len(a & b) / max(1, len(a | b))


def cluster(signals: list[dict]) -> list[dict]:
    by_src = defaultdict(list)
    for s in signals:
        by_src[s["source"].split("/")[0]].append(s)
    for grp in by_src.values():
        pr = _pct_rank([g["score"] for g in grp])
        for g, p in zip(grp, pr):
            g["pct"] = p if len(grp) > 1 else 0.6
    clusters = []
    for s in sorted(signals, key=lambda x: -x["pct"]):
        tk = set(util.tokens(s["title"]))
        for c in clusters:
            sh = len(tk & c["tokens"])
            if _jaccard(tk, c["tokens"]) >= 0.3 or (sh >= 2 and _jaccard(tk, c["tokens"]) >= 0.15):
                c["signals"].append(s); c["tokens"] |= tk
                break
        else:
            clusters.append({"tokens": set(tk), "signals": [s]})
    return clusters


def _hits(text, words):
    t = text.lower()
    return sum(1 for w in words if w in t)


def _clamp(v, lo=0, hi=100):
    return max(lo, min(hi, v))


def learning_multipliers() -> dict:
    """From analytics (see analytics.learn). Empty until you have real data."""
    try:
        from . import analytics
        return analytics.learn()
    except Exception:
        return {"category": {}, "style": {}, "n": 0}


def score_cluster(c: dict, interests: dict, learn: dict) -> dict:
    sigs = c["signals"]
    lead = max(sigs, key=lambda s: s["pct"] * (0.5 ** (s["age_hours"] / 24)))
    topic = lead["title"]
    text = " ".join(s["title"] + " " + (s.get("summary") or "") for s in sigs)
    cats = defaultdict(float)
    for s in sigs:
        cats[s["category"]] += s["pct"]
    cat = max(cats, key=cats.get)
    srcs = {s["source"].split("/")[0] for s in sigs}

    interest_raw = max(s["pct"] * (0.5 ** (s["age_hours"] / 24)) for s in sigs)
    interest = _clamp(100 * interest_raw * (1 + 0.25 * (len(srcs) - 1)))
    cover = sum(1 for s in sigs if s["source"].split("/")[0] in ("news", "youtube"))
    comp = _clamp(100 - 20 * cover)
    comps = {
        "relevance": 100 * interests.get(cat, 0.5),
        "interest": interest,
        "competition": comp,
        "shareability": _clamp(25 + 15 * _hits(text, SURPRISE) + (15 if re.search(r"\d", topic) else 0)),
        "comments": _clamp(20 + 20 * _hits(text, DEBATE) + (15 if "?" in topic else 0)),
        "story": _clamp(20 + 20 * _hits(text, NARR)),
        "hook": _clamp(30 + (20 if re.search(r"\d", text) else 0) + (15 if "?" in text or "why" in text.lower() else 0) + 12 * _hits(text, ["vanish", "impossible", "never", "three", "secret"])),
        "retention": _clamp(45 + (20 if cat in ("mysteries", "storytelling", "what-if", "facts") else 5) + (10 if len(topic.split()) <= 12 else 0)),
    }
    base = sum(WEIGHTS[k] * comps[k] for k in WEIGHTS)
    mult = learn.get("category", {}).get(cat, 1.0)
    score = _clamp(base * mult)
    why = []
    top = sorted(((WEIGHTS[k] * comps[k], k) for k in WEIGHTS), reverse=True)[:3]
    for _, k in top:
        why.append(f"{k} {comps[k]:.0f}/100")
    expl = (f"Ranked on {', '.join(why)}. Seen in {len(srcs)} source type(s): {', '.join(sorted(srcs))}. "
            + (f"Your own past results nudge '{cat}' by x{mult:.2f} (n={learn.get('n',0)} videos). " if mult != 1.0 else "No performance history applied yet. ")
            + "A higher score means 'worth trying first', not 'will get views'.")
    return {"topic": topic, "category": cat, "format": FORMAT_BY_CAT.get(cat, "explainer"), "score": round(score, 1),
            "breakdown": {k: round(v, 1) for k, v in comps.items()}, "learning_multiplier": mult, "why": expl,
            "signals": [{"source": s["source"], "title": s["title"], "url": s.get("url", ""), "summary": (s.get("summary") or "")[:300]} for s in sigs[:8]]}


def hook_suggestions(topic: str, cat: str) -> list[str]:
    t = topic.rstrip("?.!")
    t = re.sub(r"^TIL( that)?\s+", "", t, flags=re.I)
    return {
        "facts": [f"This sounds fake, but it's true: {t}.", f"Nobody told you this about {t.lower()[:60]}."],
        "mysteries": [f"{t}. And nobody can fully explain it.", "This case still doesn't add up."],
        "what-if": [f"{t}? Here's what would really happen, hour by hour."],
        "comedy": [f"Wait for it: {t}."],
    }.get(cat, [f"Here's the part of '{t[:60]}' that actually matters.", f"Stop scrolling: {t[:70]}."])


def generate(run_id: int | None = None, settings: dict | None = None, top_n=15) -> list[dict]:
    s = settings or config.load()
    sigs = research.latest_signals(run_id)
    if not sigs:
        return []
    rid = sigs[0]["run_id"]
    kept, excluded = [], []
    for g in sigs:
        (excluded if SENSITIVE.search(g["title"] + " " + (g.get("summary") or "")) else kept).append(g)
    learn = learning_multipliers()
    ideas = [score_cluster(c, s["interests"], learn) for c in cluster(kept)]
    ideas.sort(key=lambda i: -i["score"])
    ideas = ideas[:top_n]
    db.x("DELETE FROM ideas WHERE run_id=? AND status='new'", (rid,))
    for i in ideas:
        i["hook"] = hook_suggestions(i["topic"], i["category"])[0]
        i["id"] = db.x("INSERT INTO ideas(run_id,topic,category,format,hook,score,breakdown,why,signals,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (rid, i["topic"], i["category"], i["format"], i["hook"], i["score"], json.dumps(i["breakdown"]), i["why"], json.dumps(i["signals"]), time.time()))
        i["run_id"] = rid
    ideas_meta = {"excluded_sensitive": [g["title"] for g in excluded]}
    for i in ideas:
        i["meta"] = ideas_meta
    return ideas


def list_ideas(run_id: int | None = None):
    if run_id is None:
        r = db.q("SELECT id FROM research_runs ORDER BY id DESC LIMIT 1")
        if not r:
            return []
        run_id = r[0]["id"]
    rows = db.q("SELECT * FROM ideas WHERE run_id=? ORDER BY score DESC", (run_id,))
    for r in rows:
        r["breakdown"] = db.jl(r["breakdown"]); r["signals"] = db.jl(r["signals"])
    return rows


def pick(ideas: list[dict], avoid_topics: list[str]) -> dict | None:
    """Select the top idea that isn't a near-duplicate of something we already made recently."""
    av = [set(util.tokens(t)) for t in avoid_topics]
    for i in ideas:
        tk = set(util.tokens(i["topic"]))
        if all(_jaccard(tk, a) < 0.4 for a in av):
            return i
    return None
