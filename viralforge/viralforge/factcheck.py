"""Claim verification against gathered notes (local, deterministic).

For each claim in the script we find the best-supporting note sentence by content-word overlap and require
numbers in the claim to appear in that note. Result per claim: supported / weak / unsupported, plus how many
independent sources back it. This is a *traceability* check, not truth detection: if the notes are wrong the
claim passes. Treat 'single-source' as 'verify before posting'.
"""
from __future__ import annotations
import re
from . import util

NUM = re.compile(r"\d[\d,\.]*")


def _nums(s):
    return {n.strip(",.").replace(",", "") for n in NUM.findall(s)}


WORDNUM = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10"}


def _norm_nums(s):
    out = _nums(s)
    for w in re.findall(r"[a-z]+", s.lower()):
        if w in WORDNUM:
            out.add(WORDNUM[w])
    return out


def check_claim(claim: str, facts: list[dict]) -> dict:
    ct = set(util.tokens(claim))
    if not ct:
        return {"claim": claim, "status": "unsupported", "support": [], "sources": 0, "overlap": 0.0}
    scored = []
    for f in facts:
        ft = set(util.tokens(f["text"]))
        ov = len(ct & ft) / len(ct)
        nums_ok = _norm_nums(claim) <= _norm_nums(f["text"]) | _norm_nums(" ".join(x["text"] for x in facts if x["source"] == f["source"]))
        scored.append((ov if nums_ok else ov * 0.5, f))
    scored.sort(key=lambda x: -x[0])
    good = [f for ov, f in scored if ov >= 0.6]
    best = scored[0][0] if scored else 0.0
    srcs = {f["source"] for f in good}
    status = "supported" if good else ("weak" if best >= 0.4 else "unsupported")
    return {"claim": claim, "status": status, "overlap": round(best, 2), "support": [f["id"] for f in good[:3]], "sources": len(srcs)}


def check_script(script: dict, notes: dict) -> dict:
    claims = script.get("claims") or []
    if script.get("style") in ("story", "comedy", "what_if") and script.get("fictional"):
        return {"applicable": False, "results": [], "summary": "fictional/speculative content: factual claims not applicable", "ok": True, "counts": {}}
    facts = notes.get("facts", [])
    res = [check_claim(c if isinstance(c, str) else c["text"], facts) for c in claims]
    counts = {k: sum(1 for r in res if r["status"] == k) for k in ("supported", "weak", "unsupported")}
    counts["single_source"] = sum(1 for r in res if r["status"] == "supported" and r["sources"] < 2)
    ok = bool(res) and counts["unsupported"] == 0 and counts["weak"] == 0
    return {"applicable": True, "results": res, "counts": counts, "ok": ok,
            "summary": f"{counts['supported']}/{len(res)} claims traceable to notes; {counts['weak']} weak, {counts['unsupported']} unsupported; "
                       f"{counts['single_source']} rest on a single source"}
