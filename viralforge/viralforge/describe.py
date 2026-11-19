"""Instagram caption + hashtags. Plain, honest, no engagement-bait lies; includes sources and an AI-disclosure line."""
from __future__ import annotations
import re
from . import util

CAT_TAGS = {"facts": ["didyouknow", "funfacts", "mindblown"], "educational": ["learnsomethingnew", "science", "explained"],
            "technology": ["tech", "technews", "innovation"], "mysteries": ["mystery", "unsolved", "storytime"],
            "what-if": ["whatif", "thoughtexperiment", "curiosity"], "comedy": ["funny", "comedy", "relatable"],
            "gaming": ["gaming", "gamer", "gamingreels"], "roblox": ["roblox", "robloxedit", "robloxgame"],
            "challenges": ["challenge", "trynottolaugh"], "internet trends": ["trending", "viral", "internetculture"],
            "storytelling": ["storytime", "story", "reels"], "animated stories": ["animation", "animatedstory", "storytime"]}


def build(script: dict, idea_topic: str, category: str, sources: list[dict], disclose_ai: bool = True) -> dict:
    hook = script["hook"].strip()
    body = [sc["narration"] for sc in script["scenes"][1:-1]][:2]
    summary = " ".join(body)
    if len(summary) > 260:
        summary = summary[:257].rsplit(" ", 1)[0] + "..."
    cta = script.get("cta") or "What do you think? Tell me in the comments."
    cites = [f"{s['title']}" for s in sources if s.get("trust") in ("encyclopedia", "user", "news")][:3]
    kw = [re.sub(r"[^a-z0-9]", "", k) for k in (script.get("hashtags_seed") or util.tokens(idea_topic))]
    tags = []
    for t in CAT_TAGS.get(category, ["reels"]) + [k for k in kw if 3 <= len(k) <= 20] + ["reels", "explore"]:
        if t and t not in tags:
            tags.append(t)
    tags = tags[:10]
    parts = [hook, "", summary, "", cta]
    if cites:
        parts += ["", "Sources: " + "; ".join(cites)]
    if disclose_ai:
        parts += ["", "Made with AI-generated visuals and voice."]
    parts += ["", " ".join("#" + t for t in tags)]
    text = "\n".join(parts)
    return {"caption": text, "hashtags": tags, "length": len(text)}
