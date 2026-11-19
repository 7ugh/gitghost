"""Script generation: local LLM (Ollama) when available, deterministic template fallback for factual explainers.

Script schema:
{title, style, fictional, mood, hook, scenes:[{narration, text, motif, stat?, claim_ids}], claims:[str], cta, hashtags_seed}
"""
from __future__ import annotations
import json, re, urllib.request
from . import config, util

STYLES = {
    "explainer": {"words": (70, 130), "desc": "fast factual explainer, every fact from the notes"},
    "story": {"words": (90, 160), "desc": "short narrated story with a reveal; may be documented or clearly fictional"},
    "what_if": {"words": (80, 140), "desc": "speculative 'what if' walk-through, reasoning from real science, flagged as speculative"},
    "comedy": {"words": (50, 100), "desc": "original comedic bit with a setup, escalation and a punchline"},
}
MOTIFS = ["orbs", "heart", "star", "bars", "ring", "spark", "question", "bolt", "stat", "moon", "wave"]


# ------------------------------------------------------------------ LLM
class Ollama:
    def __init__(self, url, model):
        self.url, self.model = url.rstrip("/"), model

    @staticmethod
    def models(url):
        try:
            d = util.fetch_json(url.rstrip("/") + "/api/tags", timeout=3)
            return [m["name"] for m in d.get("models", [])]
        except Exception:
            return []

    def chat_json(self, system, user, temperature=0.8):
        body = json.dumps({"model": self.model, "stream": False, "format": "json", "options": {"temperature": temperature},
                           "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}).encode()
        req = urllib.request.Request(self.url + "/api/chat", body, {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=600) as r:
            txt = json.load(r)["message"]["content"]
        return json.loads(txt)


def get_llm(settings=None):
    s = (settings or config.load())["llm"]
    if s["backend"] == "template":
        return None
    models = Ollama.models(s["ollama_url"])
    if not models:
        return None
    from . import hardware
    want = s["model"] or ""
    pick = want if want in models else next((m for m in models if "instruct" in m or "qwen" in m or "llama" in m), models[0])
    return Ollama(s["ollama_url"], pick)


SYSTEM = """You are a short-form video scriptwriter for Instagram Reels for a general audience (family-friendly, no gore, no hate, no politics).
Write ORIGINAL scripts in your own words. Never copy sentences from the notes. Spoken, conversational, punchy.
Hard rules: (1) open with a hook in the first sentence (<= 14 words) that the video truly pays off; (2) every factual statement must be
supported by the provided NOTES; do not add facts that are not in the notes; (3) each 'claims' entry is one checkable factual sentence
using the same facts and numbers as the narration; (4) end with a meaningful closing line plus a question inviting comments.
Return ONLY JSON: {"title":str,"mood":"upbeat|mysterious|chill|epic","hook":str,"scenes":[{"narration":str,"text":str(<=5 words on screen),"motif":one of %s,"stat":optional str like "3"}],"claims":[str],"cta":str,"hashtags_seed":[str]}""" % MOTIFS


def llm_script(llm, idea: dict, notes: dict, style: str, feedback: str = "") -> dict:
    lo, hi = STYLES[style]["words"]
    facts = "\n".join(f"[{f['id']}] {f['text']}" for f in notes.get("facts", [])[:30])
    user = (f"TOPIC: {idea['topic']}\nSTYLE: {style} - {STYLES[style]['desc']}\nTARGET LENGTH: {lo}-{hi} spoken words, 6-9 scenes.\n"
            f"NOTES:\n{facts or '(none: fictional/speculative content only; set no factual claims)'}\n"
            + (f"VIEWER FEEDBACK TO APPLY: {feedback}\n" if feedback else ""))
    d = llm.chat_json(SYSTEM, user)
    return normalize(d, idea, style, fictional=(style in ("story", "comedy", "what_if") and not notes.get("facts")))


# ------------------------------------------------------------------ template fallback
def _shorten(t: str, n=32) -> str:
    """Never cut a sentence mid-clause (that can invert its meaning): keep it whole up to n words, else cut at the last comma/semicolon."""
    w = t.split()
    if len(w) <= n:
        return t
    head = " ".join(w[:n])
    cut = max(head.rfind(", "), head.rfind("; "))
    if cut > len(head) // 2:
        return head[:cut].rstrip(",;") + "."
    return t


def _keyphrase(t: str) -> str:
    toks = [w for w in re.findall(r"[A-Za-z0-9\-']+", t) if w.lower() not in util.STOPWORDS]
    return " ".join(toks[:3]).upper() if toks else t[:24].upper()


def template_script(idea: dict, notes: dict, style: str = "explainer", feedback: str = "") -> dict:
    """Explainer from notes. Quality is limited by the notes; an LLM does much better paraphrasing."""
    facts = [f for f in notes.get("facts", []) if 25 <= len(f["text"]) <= 260]
    if style != "explainer" or len(facts) < 3:
        raise RuntimeError(f"template fallback only writes factual explainers with >=3 notes (style={style}, notes={len(facts)}). "
                           "Install Ollama + a model for story/comedy/what-if styles, or supply --notes.")
    facts = facts[:6]
    topic = re.sub(r"^TIL( that)?\s+", "", idea["topic"], flags=re.I).rstrip("?.!")
    scenes = [{"narration": f"{topic[:1].upper() + topic[1:90]}. Sounds made up, but it's real.", "text": "SOUNDS FAKE?", "motif": "question", "claim_ids": []}]
    claims = []
    motifs = ["heart", "orbs", "wave", "bars", "star", "spark"]
    for i, f in enumerate(facts):
        n = _shorten(f["text"])
        m = re.search(r"\b(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\b", " ".join(n.split()[:6]), re.I)  # quantity near sentence start only
        sc = {"narration": n, "text": _keyphrase(n), "motif": motifs[i % len(motifs)], "claim_ids": [f["id"]]}
        if m:
            sc["motif"], sc["stat"] = "stat", WORDNUM_DISPLAY.get(m.group(1).lower(), m.group(1))
        scenes.append(sc)
        claims.append(n)
    scenes.append({"narration": "Once you know that, you can't unsee it. Which of these surprised you most? Tell me in the comments.", "text": "YOUR TURN", "motif": "spark", "claim_ids": []})
    return normalize({"title": topic[:80], "mood": "upbeat", "hook": scenes[0]["narration"], "scenes": scenes, "claims": claims,
                      "cta": "Which fact surprised you most?", "hashtags_seed": util.tokens(topic)[:4]}, idea, "explainer", fictional=False)


WORDNUM_DISPLAY = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10"}


def normalize(d: dict, idea: dict, style: str, fictional=False) -> dict:
    scenes = []
    for sc in d.get("scenes", []):
        nar = re.sub(r"\s+", " ", str(sc.get("narration", ""))).strip()
        if not nar:
            continue
        motif = sc.get("motif") if sc.get("motif") in MOTIFS else "orbs"
        o = {"narration": nar, "text": str(sc.get("text") or _keyphrase(nar))[:40].upper(), "motif": motif, "claim_ids": sc.get("claim_ids", [])}
        for k in ("stat", "image", "clip", "clip_license"):
            if sc.get(k):
                o[k] = str(sc[k])
        scenes.append(o)
    if len(scenes) < 3:
        raise ValueError("script has fewer than 3 scenes")
    return {"title": str(d.get("title") or idea["topic"])[:90], "style": style, "fictional": bool(d.get("fictional", fictional)),
            "mood": d.get("mood") if d.get("mood") in ("upbeat", "mysterious", "chill", "epic") else "upbeat",
            "hook": d.get("hook") or scenes[0]["narration"], "scenes": scenes,
            "claims": [c if isinstance(c, str) else c.get("text", "") for c in d.get("claims", [])],
            "cta": d.get("cta", ""), "hashtags_seed": [str(x) for x in d.get("hashtags_seed", [])][:6],
            "word_count": sum(len(s["narration"].split()) for s in scenes)}


def write_script(idea: dict, notes: dict, style: str | None = None, settings=None, feedback: str = "") -> tuple[dict, str]:
    style = style or idea.get("format", "explainer")
    llm = get_llm(settings)
    if llm:
        try:
            return llm_script(llm, idea, notes, style, feedback), f"ollama:{llm.model}"
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            if style != "explainer":
                raise RuntimeError(f"LLM failed ({err}) and no template exists for style '{style}'")
    return template_script(idea, notes, style, feedback), "template"
