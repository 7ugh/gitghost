"""End-to-end: idea -> notes -> script -> factcheck -> narration -> music -> captions -> render -> thumbnail -> description -> QC -> approval queue.
The pipeline NEVER publishes. It ends at status 'pending_approval'."""
from __future__ import annotations
import json, pathlib, shutil, time, uuid
from . import analytics, audio, config, db, describe, factcheck, ideas as ideas_mod, notes as notes_mod, qc, render, research, scriptgen, thumbnail, tts, util


def _save_project(vdir, project):
    (vdir / "project.json").write_text(json.dumps(project, indent=2))


def load_project(vid: str) -> dict:
    return json.loads((config.video_dir(vid) / "project.json").read_text())


def create_video(idea: dict | None = None, topic: str | None = None, style: str | None = None, notes_file: str | None = None,
                 voice_id: str | None = None, feedback: str = "", progress=None, settings=None, vid: str | None = None,
                 category: str | None = None, script_override: dict | None = None, fps: int | None = None) -> str:
    s = settings or config.load()
    P = progress or (lambda stage, frac=None: None)
    idea = idea or {"topic": topic, "category": category or research.classify(topic or ""), "format": style or "explainer", "signals": [], "id": None}
    style = style or idea.get("format") or "explainer"
    vid = vid or time.strftime("%Y%m%d-%H%M%S-") + util.slugify(idea["topic"], 28)
    vdir = config.video_dir(vid)
    existing = db.get_video(vid)
    if not existing:
        db.x("INSERT INTO videos(id,idea_id,topic,category,style,status,title,created_at,updated_at,feedback) VALUES(?,?,?,?,?,?,?,?,?,?)",
             (vid, idea.get("id"), idea["topic"], idea["category"], style, "generating", idea["topic"], time.time(), time.time(), feedback))
    else:
        db.update_video(vid, status="generating", feedback=feedback)
    try:
        P("research notes")
        notes = notes_mod.gather(idea["topic"], idea.get("signals"), notes_file)
        P("script")
        if script_override:
            script, backend = scriptgen.normalize(script_override, idea, style, fictional=script_override.get("fictional", False)), "edited"
        else:
            script, backend = scriptgen.write_script(idea, notes, style, s, feedback)
        P("fact check")
        fc = factcheck.check_script(script, notes)
        P("narration")
        voice = None
        if voice_id:
            voice = next((v for v in tts.list_voices() if v["id"] == voice_id or v["name"] == voice_id), None)
        voice = voice or tts.pick_voice(s)
        timeline = tts.narrate(script, vdir, voice, s["tts"].get("rate", 1.0))
        P("music + sfx")
        mix = audio.mix(vdir, timeline, script["mood"], seed=abs(hash(vid)) % 1000)
        P("captions")
        caps = render.build_captions(timeline)
        project = {"id": vid, "idea": {k: idea.get(k) for k in ("id", "topic", "category", "format", "score", "breakdown", "why", "hook", "signals")},
                   "script": script, "notes": notes, "factcheck": fc, "timeline": timeline, "captions": caps, "voice": voice, "script_backend": backend,
                   "assets": [{"scene": i, "path": sc.get(k), "license": sc.get("clip_license") or sc.get("image_license")}
                              for i, sc in enumerate(script["scenes"]) for k in ("image", "clip") if sc.get(k)],
                   "feedback": feedback, "fps": fps or s["video"]["fps"], "thumb_time": min(1.6, timeline["total"] - 0.2)}
        _save_project(vdir, project)
        _render_and_finish(vid, project, s, P)
        return vid
    except Exception as e:
        db.update_video(vid, status="failed", meta={"error": f"{type(e).__name__}: {e}"})
        db.log_event(vid, "failed", str(e)[:500])
        raise


def _render_and_finish(vid, project, s, P):
    vdir = config.video_dir(vid)
    script, timeline = project["script"], project["timeline"]
    r = render.Renderer(script, timeline, project["captions"], vdir, fps=project["fps"])
    try:
        P("render", 0.0)
        out = vdir / "final.mp4"
        render.encode(r, str(vdir / "mix.wav"), str(out), fps=project["fps"], crf=s["video"]["crf"], preset=s["video"]["preset"],
                      encoder=s["video"]["encoder"], progress=lambda f: P("render", f))
        project["min_caption_font"] = r.min_caption_font
        P("thumbnail")
        thumbnail.make(r, script, vdir, project.get("thumb_time"), project.get("thumb_title"))
        (vdir / "captions.srt").write_text(render.to_srt(project["captions"]))
    finally:
        r.close()
    d = describe.build(script, project["idea"]["topic"], project["idea"]["category"], project["notes"].get("sources", []), s["video"]["disclose_ai"])
    if project.get("description_override"):
        d["caption"] = project["description_override"]; d["length"] = len(d["caption"])
    project["description"] = d
    P("quality checks")
    project["qc"] = qc.run(str(vdir / "final.mp4"), project)
    rec = analytics.recommend_time(s)
    project["recommended_time"] = rec
    _save_project(vdir, project)
    meta = {"description": d, "recommended_time": rec, "voice": project["voice"], "script_backend": project["script_backend"],
            "sources": project["notes"].get("sources", []), "idea_why": project["idea"].get("why"), "thumb_offset_ms": int(project["thumb_time"] * 1000)}
    db.update_video(vid, status="pending_approval", title=script["title"], qc=project["qc"], meta=meta, duration=project["timeline"]["total"])
    db.log_event(vid, "ready_for_approval", f"qc={project['qc']['overall']}")


def re_render(vid: str, settings=None, progress=None, retts=False):
    """After EDIT: rebuild from project.json (captions/script/thumbnail/description edits). Re-voices only if narration changed."""
    s = settings or config.load()
    P = progress or (lambda *a: None)
    project = load_project(vid); vdir = config.video_dir(vid)
    db.update_video(vid, status="generating")
    try:
        if retts:
            P("narration")
            voice = project["voice"]
            project["timeline"] = tts.narrate(project["script"], vdir, voice, s["tts"].get("rate", 1.0))
            audio.mix(vdir, project["timeline"], project["script"]["mood"], seed=abs(hash(vid)) % 1000)
            project["captions"] = render.build_captions(project["timeline"])
            project["factcheck"] = factcheck.check_script(project["script"], project["notes"])
        _render_and_finish(vid, project, s, P)
    except Exception as e:
        db.update_video(vid, status="failed", meta={"error": str(e)[:300]}); raise


def run_daily(settings=None, progress=None, fixture: str | None = None, notes_file: str | None = None, dry=False) -> dict:
    """research -> ideas -> rank -> pick -> create -> pending_approval. One video only; never publishes; never auto-replaces rejected ones."""
    s = settings or config.load()
    P = progress or (lambda *a: None)
    P("research")
    res = research.run_research(s, fixture=fixture)
    P("ideas")
    ranked = ideas_mod.generate(res["run_id"], s)
    if not ranked:
        return {"ok": False, "reason": "no research signals available (all sources failed?)", "research": res}
    recent = [v["topic"] for v in db.q("SELECT topic FROM videos WHERE created_at>?", (time.time() - 21 * 86400,))]
    can_llm = scriptgen.get_llm(s) is not None
    cands = [i for i in ranked if can_llm or i["format"] == "explainer"]   # no LLM => factual explainers only
    choice = ideas_mod.pick(cands, recent)
    if not choice:
        return {"ok": False, "reason": "all top ideas were recently used or need an LLM (install Ollama)", "ideas": ranked[:5]}
    if dry:
        return {"ok": True, "dry": True, "choice": choice, "ranked": ranked}
    vid = create_video(idea=choice, notes_file=notes_file, progress=P, settings=s)
    return {"ok": True, "video_id": vid, "choice": choice}


def apply_edit(vid: str, edit: dict, settings=None, progress=None) -> dict:
    """EDIT: script text, on-screen text, captions, description, thumbnail title/time, narrator. Re-voices/re-renders only what changed.
    Always returns the video to 'pending_approval' (an edit voids any earlier approval)."""
    s = settings or config.load()
    P = progress or (lambda *a: None)
    project = load_project(vid); vdir = config.video_dir(vid)
    retts = rerender = False
    if "scenes" in edit:       # [{narration?, text?}] aligned with script scenes
        for sc, e in zip(project["script"]["scenes"], edit["scenes"]):
            if e.get("narration") and e["narration"].strip() != sc["narration"]:
                sc["narration"] = e["narration"].strip(); retts = True
            if e.get("text") is not None and e["text"] != sc["text"]:
                sc["text"] = e["text"].upper(); rerender = True
    if edit.get("voice"):
        v = next((x for x in tts.list_voices() if x["id"] == edit["voice"] or x["name"] == edit["voice"]), None)
        if v and v["id"] != project["voice"].get("id"):
            project["voice"] = v; retts = True
    if "captions" in edit and not retts:
        for c, e in zip(project["captions"], edit["captions"]):
            old = " ".join(w["w"] for w in c["words"])
            if e.strip() and e.strip() != old:
                c.update(render.retime_chunk(c, e.strip())); rerender = True
    if edit.get("title"):
        project["script"]["title"] = edit["title"]
    if edit.get("description"):
        project["description_override"] = edit["description"]
    if "thumb_title" in edit:
        project["thumb_title"] = edit["thumb_title"] or None
    if "thumb_time" in edit:
        project["thumb_time"] = max(0.0, min(float(edit["thumb_time"]), project["timeline"]["total"] - 0.1))
    _save_project(vdir, project)
    db.log_event(vid, "edited", json.dumps({k: (v if k in ("voice", "title", "thumb_title", "thumb_time") else "...") for k, v in edit.items()})[:300])
    if retts or rerender:
        re_render(vid, s, progress, retts=retts)
        return {"mode": "re-rendered" + (" with new narration" if retts else "")}
    # thumbnail / description only: no re-encode
    P("thumbnail")
    r = render.Renderer(project["script"], project["timeline"], project["captions"], vdir, fps=project["fps"])
    try:
        thumbnail.make(r, project["script"], vdir, project.get("thumb_time"), project.get("thumb_title"))
    finally:
        r.close()
    d = describe.build(project["script"], project["idea"]["topic"], project["idea"]["category"], project["notes"].get("sources", []), s["video"]["disclose_ai"])
    if project.get("description_override"):
        d["caption"] = project["description_override"]; d["length"] = len(d["caption"])
    project["description"] = d
    project["qc"] = qc.run(str(vdir / "final.mp4"), project)
    _save_project(vdir, project)
    v = db.get_video(vid)
    db.update_video(vid, status="pending_approval", title=project["script"]["title"], qc=project["qc"],
                    meta={**v["meta"], "description": d, "thumb_offset_ms": int(project["thumb_time"] * 1000)})
    return {"mode": "metadata only"}
