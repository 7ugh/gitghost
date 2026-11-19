"""Dashboard + JSON API (stdlib only). Binds to localhost by default. To use from your phone: `serve --host 0.0.0.0`; the access
token is then required (printed at startup, stored as a cookie). Every state-changing request needs the X-VF header (CSRF guard)."""
from __future__ import annotations
import datetime as dt, http.cookies, json, mimetypes, os, pathlib, re, secrets, threading, time, traceback, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from . import analytics, config, db, hardware, ideas as ideas_mod, pipeline, publish, research, scheduler, scriptgen, tts

WEB = pathlib.Path(__file__).parent / "web"
JOBS: dict[str, dict] = {}
RENDER_LOCK = threading.Lock()          # one heavy job at a time
PUB_TOKENS: dict[str, tuple[str, float]] = {}


def start_job(fn, label: str) -> str:
    jid = secrets.token_hex(4)
    job = {"id": jid, "label": label, "state": "queued", "stage": "queued", "frac": 0, "result": None, "error": None, "started": time.time()}
    JOBS[jid] = job

    def prog(stage, frac=None):
        job["stage"], job["frac"] = stage, frac or 0

    def run():
        with RENDER_LOCK:
            job["state"] = "running"
            try:
                job["result"] = fn(prog)
                job["state"] = "done"
            except Exception as e:
                job["state"], job["error"] = "error", f"{type(e).__name__}: {e}"
                traceback.print_exc()
    threading.Thread(target=run, daemon=True).start()
    return jid


def video_view(v: dict, detail=False) -> dict:
    out = {k: v[k] for k in ("id", "topic", "category", "style", "status", "title", "created_at", "scheduled_at", "posted_at", "permalink", "duration", "feedback")}
    out["qc"] = v["qc"]; out["approved"] = publish.is_approved(v["id"])
    out["meta"] = {k: v["meta"].get(k) for k in ("description", "recommended_time", "voice", "script_backend", "sources", "idea_why", "error", "publish_error")}
    out["media"] = {"video": f"/media/{v['id']}/final.mp4", "thumb": f"/media/{v['id']}/thumb.jpg"}
    if detail:
        p = pipeline.load_project(v["id"])
        out["project"] = {"script": p["script"], "captions": p["captions"], "factcheck": p["factcheck"], "notes": p["notes"], "idea": p["idea"],
                          "thumb_time": p.get("thumb_time"), "timeline_total": p["timeline"]["total"]}
        out["events"] = db.q("SELECT ts,kind,detail FROM events WHERE video_id=? ORDER BY id", (v["id"],))
    return out


class H(BaseHTTPRequestHandler):
    server_version = "ViralForge/0.1"

    def log_message(self, *a):
        pass

    # ---- plumbing
    def _json(self, obj, code=200):
        b = json.dumps(obj, default=str).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(b)

    def _err(self, code, msg):
        self._json({"error": msg}, code)

    def _auth(self) -> bool:
        s = self.server.settings
        if self.server.local_only:
            return True
        tok = s["server"]["token"]
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if q.get("token", [""])[0] == tok:
            self._set_cookie = True
            return True
        c = http.cookies.SimpleCookie(self.headers.get("Cookie", ""))
        return c.get("vf_token") is not None and secrets.compare_digest(c["vf_token"].value, tok)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def _route(self, method):
        self._set_cookie = False
        u = urllib.parse.urlparse(self.path); path = u.path
        pm = re.match(r"^/pub/([^/]+)/([^/]+)\.mp4$", path)           # temporary public URL for Instagram video_url mode
        if pm and method == "GET":
            ent = PUB_TOKENS.get(pm.group(2))
            if ent and ent[0] == pm.group(1) and ent[1] > time.time():
                return self._file(config.video_dir(pm.group(1)) / "final.mp4")
            return self._err(404, "expired")
        if not self._auth():
            return self._err(401, "token required (open the URL printed by `serve`, including ?token=...)")
        if method == "POST" and self.headers.get("X-VF") != "1":
            return self._err(403, "missing X-VF header")
        try:
            if path in ("/", "/index.html"):
                return self._static("index.html")
            if path.startswith("/static/"):
                return self._static(path[8:])
            if path.startswith("/media/"):
                _, _, vid, fn = path.split("/", 3)
                if fn not in ("final.mp4", "thumb.jpg", "captions.srt", "manual_package.zip") or "/" in vid or ".." in vid:
                    return self._err(404, "no such file")
                return self._file(config.video_dir(vid) / fn, download=fn.endswith(("zip", "srt")))
            if path.startswith("/api/"):
                body = self._body() if method == "POST" else {}
                res = self.api(method, path[5:], urllib.parse.parse_qs(u.query), body)
                if isinstance(res, tuple):
                    return self._json(res[1], res[0])
                return self._json(res)
            self._err(404, "not found")
        except publish.NotApproved as e:
            self._err(403, str(e))
        except KeyError as e:
            self._err(404, f"not found: {e}")
        except Exception as e:
            traceback.print_exc()
            self._err(500, f"{type(e).__name__}: {e}")

    def _static(self, name):
        p = (WEB / name).resolve()
        if WEB.resolve() not in p.parents or not p.exists():
            return self._err(404, "not found")
        self._file(p, cookie=True)

    def _file(self, p: pathlib.Path, download=False, cookie=False):
        if not p.exists():
            return self._err(404, "missing")
        size = p.stat().st_size
        ctype = mimetypes.guess_type(str(p))[0] or "application/octet-stream"
        rng = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
        start, end, code = 0, size - 1, 200
        if rng and (rng.group(1) or rng.group(2)):
            if rng.group(1):
                start = int(rng.group(1)); end = int(rng.group(2)) if rng.group(2) else end
            else:
                start = max(0, size - int(rng.group(2)))
            end = min(end, size - 1); code = 206
        self.send_response(code)
        self.send_header("Content-Type", ctype); self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if code == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        if download:
            self.send_header("Content-Disposition", f'attachment; filename="{p.name}"')
        if cookie and self._set_cookie:
            self.send_header("Set-Cookie", f"vf_token={self.server.settings['server']['token']}; Path=/; HttpOnly; SameSite=Strict; Max-Age=31536000")
        self.send_header("Cache-Control", "no-cache"); self.end_headers()
        try:
            with open(p, "rb") as f:
                f.seek(start); left = end - start + 1
                while left > 0:
                    chunk = f.read(min(1 << 20, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk); left -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass

    # ---- API
    def api(self, method, route, qs, body):
        s = config.load()
        parts = route.strip("/").split("/")
        if route == "status":
            counts = {r["status"]: r["n"] for r in db.q("SELECT status, COUNT(*) n FROM videos GROUP BY status")}
            llm = scriptgen.get_llm(s)
            ok, why = publish.ig_ready(s)
            due, dwhy = scheduler.daily_due(s)
            try:
                voice = tts.pick_voice(s)["name"]
            except Exception as e:
                voice = f"NONE ({e})"
            return {"counts": counts, "llm": f"ollama:{llm.model}" if llm else "none (template fallback: factual explainers only)", "voice": voice,
                    "instagram": {"ready": ok, "detail": why}, "daily": {"enabled": s["daily"]["enabled"], "time": s["daily"]["time"], "due": due, "why": dwhy},
                    "busy": [j for j in JOBS.values() if j["state"] in ("queued", "running")][:1]}
        if route == "hardware":
            return hardware.report()
        if route == "voices":
            return {"voices": tts.list_voices(), "current": s["tts"]}
        if route == "research" and method == "GET":
            runs = db.q("SELECT * FROM research_runs ORDER BY id DESC LIMIT 1")
            return {"run": ({**runs[0], "source_status": json.loads(runs[0]["source_status"])} if runs else None), "signals": research.latest_signals()[:80],
                    "manual": db.q("SELECT * FROM manual_signals ORDER BY id DESC LIMIT 20")}
        if route == "research/run":
            fx = str(pathlib.Path(__file__).parent / "fixtures" / "sample_signals.json") if body.get("sample") else None
            jid = start_job(lambda P: (P("researching"), research.run_research(s, fixture=fx), P("ranking"), len(ideas_mod.generate()))[-1], "research")
            return {"job": jid}
        if route == "research/manual":
            research.add_manual(body.get("platform", "tiktok"), body["title"], body.get("url", ""), body.get("note", ""), body.get("category", ""))
            return {"ok": True}
        if route == "ideas" and method == "GET":
            return {"ideas": ideas_mod.list_ideas(), "weights": ideas_mod.WEIGHTS, "help": ideas_mod.COMPONENT_HELP,
                    "learning": analytics.learn(), "disclaimer": "Scores prioritise what to try first. They are heuristics, not predictions; they cannot promise views."}
        if route == "ideas/rank":
            return {"count": len(ideas_mod.generate())}
        if route == "videos/create":
            idea = None
            if body.get("idea_id"):
                r = db.q("SELECT * FROM ideas WHERE id=?", (body["idea_id"],))[0]
                idea = {**r, "breakdown": db.jl(r["breakdown"]), "signals": db.jl(r["signals"])}
            topic = body.get("topic") or (idea or {}).get("topic")
            if not topic:
                return 400, {"error": "topic or idea_id required"}
            notes_file = None
            if body.get("notes_text"):
                nf = config.HOME / "tmp" / f"notes-{secrets.token_hex(3)}.json"
                nf.write_text(json.dumps(_notes_from_text(topic, body["notes_text"]))); notes_file = str(nf)
            jid = start_job(lambda P: pipeline.create_video(idea=idea, topic=topic, style=body.get("style") or None, notes_file=notes_file,
                                                           voice_id=body.get("voice") or None, progress=P, category=body.get("category") or None), "create video")
            return {"job": jid}
        if route == "videos" and method == "GET":
            status = qs.get("status", [None])[0]
            rows = db.q("SELECT id FROM videos" + (" WHERE status=?" if status else "") + " ORDER BY created_at DESC LIMIT 100", (status,) if status else ())
            return {"videos": [video_view(db.get_video(r["id"])) for r in rows]}
        if parts[0] == "videos" and len(parts) >= 2:
            vid = parts[1]
            v = db.get_video(vid)
            if not v:
                return 404, {"error": "no such video"}
            act = parts[2] if len(parts) > 2 else None
            if act is None:
                return video_view(v, detail=True)
            return self.video_action(s, v, act, body)
        if route == "analytics":
            tb = analytics.table()
            return {"table": tb, "learning": analytics.learn(), "best_hour": analytics.best_hours(), "recommend": analytics.recommend_time(s),
                    "account": db.q("SELECT fetched_at, metrics FROM metrics WHERE video_id='_account' ORDER BY id DESC LIMIT 30"),
                    "note": "Only real numbers from Instagram's API or your imports are shown. Small samples = low confidence."}
        if route == "analytics/import":
            return analytics.import_csv(body["csv"])
        if route == "analytics/sync":
            return {"job": start_job(lambda P: analytics.sync_instagram(s), "sync")}
        if route == "daily/run":
            ran = scheduler._marker().exists() and json.loads(scheduler._marker().read_text()).get("date") == dt.date.today().isoformat()
            if ran and not body.get("force"):
                return 409, {"error": "daily job already ran today. Pass force to run again (this is never done automatically)."}
            scheduler._marker().write_text(json.dumps({"date": dt.date.today().isoformat(), "at": time.time()}))
            return {"job": start_job(lambda P: pipeline.run_daily(s, progress=P, fixture=body.get("fixture")), "daily run")}
        if route == "settings":
            if method == "POST":
                new = config.merge(s, body)
                new["server"]["token"] = s["server"]["token"]
                config.save(new); s = new
            safe = json.loads(json.dumps(s)); safe["server"]["token"] = "(hidden)"
            safe["secrets_present"] = {"instagram_token": bool(os.environ.get(s["instagram"]["token_env"])),
                                       "youtube_api_key": bool(os.environ.get(s["sources"]["youtube"]["api_key_env"]))}
            safe["categories"] = config.CATEGORIES
            return safe
        if route.startswith("jobs/"):
            j = JOBS.get(parts[1])
            return j or (404, {"error": "no such job"})
        return 404, {"error": f"unknown route {route}"}

    def video_action(self, s, v, act, body):
        vid = v["id"]
        if act == "approve":
            if v["status"] != "pending_approval":
                return 409, {"error": f"cannot approve a video in status '{v['status']}'"}
            db.update_video(vid, status="approved"); db.log_event(vid, "approved", "explicit user action")
            return {"ok": True}
        if act == "unapprove":
            db.update_video(vid, status="pending_approval", scheduled_at=None); db.log_event(vid, "unapproved")
            return {"ok": True}
        if act == "reject":
            db.update_video(vid, status="rejected", feedback=body.get("reason", ""), scheduled_at=None); db.log_event(vid, "rejected", body.get("reason", ""))
            return {"ok": True, "note": "Nothing is regenerated automatically. Use REGENERATE with feedback if you want a new version."}
        if act == "regenerate":
            fb = body.get("feedback", "")
            db.log_event(vid, "regenerated", fb); db.update_video(vid, status="generating", scheduled_at=None)
            pj = pipeline.load_project(vid)
            idea = {**pj["idea"], "signals": pj["idea"].get("signals") or []}
            nf = config.HOME / "tmp" / f"notes-{vid}.json"; nf.write_text(json.dumps(pj["notes"]))
            return {"job": start_job(lambda P: pipeline.create_video(idea=idea, style=pj["script"]["style"], notes_file=str(nf), voice_id=pj["voice"].get("id"),
                                                                   feedback=fb, progress=P, vid=vid), "regenerate")}
        if act == "edit":
            return {"job": start_job(lambda P: pipeline.apply_edit(vid, body, s, P), "edit")}
        if act == "schedule":
            publish.require_approved(vid)
            at = body.get("at")
            at = dt.datetime.fromisoformat(at).timestamp() if isinstance(at, str) else float(at)
            if at < time.time() + 60:
                return 400, {"error": "schedule time must be in the future"}
            db.update_video(vid, status="scheduled", scheduled_at=at); db.log_event(vid, "scheduled", str(at))
            ok, why = publish.ig_ready(s)
            return {"ok": True, "scheduled_at": at, "auto_publish": ok, "note": None if ok else f"Auto-publish unavailable ({why}); you will need to post manually at that time."}
        if act == "unschedule":
            db.update_video(vid, status="approved", scheduled_at=None); return {"ok": True}
        if act == "post_now":
            publish.require_approved(vid)
            if body.get("confirm") is not True:
                return 400, {"error": "confirm:true required"}
            ok, why = publish.ig_ready(s)
            if not ok:
                return {"manual": True, "reason": why, "package": f"/media/{vid}/manual_package.zip" if publish.manual_package(vid) else ""}
            pub_url = None
            if s["instagram"]["mode"] == "video_url":
                tok = secrets.token_urlsafe(12); PUB_TOKENS[tok] = (vid, time.time() + 3600)
                pub_url = f"{s['instagram']['public_base_url'].rstrip('/')}/pub/{vid}/{tok}.mp4"
            return {"job": start_job(lambda P: publish.publish_instagram(vid, s, pub_url, P), "publish")}
        if act == "manual_package":
            publish.require_approved(vid)
            publish.manual_package(vid)
            return {"url": f"/media/{vid}/manual_package.zip"}
        if act == "manual_posted":
            publish.mark_posted(vid, body.get("permalink", "")); return {"ok": True}
        return 404, {"error": f"unknown action {act}"}


def _notes_from_text(topic, text):
    from . import util
    facts = [{"id": f"U{i}", "text": t, "source": "U"} for i, t in enumerate([x.strip() for x in re.split(r"\n+", text) if len(x.strip()) > 20], 1)]
    return {"topic": topic, "sources": [{"id": "U", "title": "Notes pasted by you", "url": "", "license": "user", "trust": "user"}], "facts": facts}


class Srv(ThreadingHTTPServer):
    daemon_threads = True


def make_server(host=None, port=None) -> Srv:
    s = config.load()
    host = host or s["server"]["host"]; port = port if port is not None else s["server"]["port"]
    srv = Srv((host, port), H)
    srv.settings = s
    srv.local_only = host in ("127.0.0.1", "localhost", "::1")
    return srv


def serve(host=None, port=None):
    srv = make_server(host, port)
    h, p = srv.server_address[:2]
    tok = srv.settings["server"]["token"]
    print(f"ViralForge dashboard: http://{'localhost' if srv.local_only else h}:{p}/" + ("" if srv.local_only else f"?token={tok}"))
    if not srv.local_only:
        print("  (phone: use your Mac's LAN IP instead of 0.0.0.0, same ?token=...; trusted networks only)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
