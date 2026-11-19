import json, pathlib, threading, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from viralforge import pipeline, config, db

NOTES = pathlib.Path(__file__).parent.parent / "viralforge" / "fixtures" / "octopus_notes.json"
SCRIPT = {"title": "Octopus hearts", "mood": "upbeat", "scenes": [
    {"narration": "Octopuses have three hearts. Sounds made up, but it's real.", "text": "SOUNDS FAKE?", "motif": "question"},
    {"narration": "An octopus has three hearts: two pump blood past the gills, and a third pumps it around the rest of the body.", "text": "THREE HEARTS", "motif": "heart", "stat": "3"},
    {"narration": "Which fact surprised you most? Tell me in the comments.", "text": "YOUR TURN", "motif": "spark"}],
    "claims": ["An octopus has three hearts: two pump blood past the gills, and a third pumps it around the rest of the body."],
    "cta": "Which fact surprised you most?", "hashtags_seed": ["octopus", "hearts"]}
_cache = {}


def make_video(topic="Octopus hearts", **kw) -> str:
    return pipeline.create_video(topic=topic, category="facts", notes_file=str(NOTES), script_override=json.loads(json.dumps(SCRIPT)), **kw)


class MockGraph:
    """Local stand-in for the Instagram Graph API (records every request)."""
    def __init__(self, fail_processing=False):
        self.log, self.fail = [], fail_processing
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(s, *a): pass
            def _send(s, obj, code=200):
                b = json.dumps(obj).encode(); s.send_response(code); s.send_header("Content-Length", str(len(b))); s.end_headers(); s.wfile.write(b)
            def do_POST(s):
                n = int(s.headers.get("Content-Length") or 0); body = s.rfile.read(n)
                path = urllib.parse.urlparse(s.path).path
                rec = {"method": "POST", "path": path, "headers": {k.lower(): v for k, v in s.headers.items()}}
                if path.startswith("/rupload"):
                    rec["bytes"] = len(body); outer.log.append(rec); return s._send({"success": True})
                form = urllib.parse.parse_qs(body.decode()); rec["form"] = {k: v[0] for k, v in form.items()}
                outer.log.append(rec)
                if path.endswith("/media"):
                    return s._send({"id": "CONT1", "uri": f"http://127.0.0.1:{outer.port}/rupload/CONT1"})
                if path.endswith("/media_publish"):
                    return s._send({"id": "MEDIA1"})
                s._send({"error": "unknown"}, 404)
            def do_GET(s):
                p = urllib.parse.urlparse(s.path); outer.log.append({"method": "GET", "path": p.path, "q": p.query})
                if p.path.endswith("/CONT1"):
                    return s._send({"status_code": "ERROR" if outer.fail else "FINISHED"})
                if p.path.endswith("/insights"):
                    return s._send({"data": [{"name": "reach", "values": [{"value": 1000}]}, {"name": "views", "values": [{"value": 1800}]},
                                             {"name": "likes", "values": [{"value": 90}]}, {"name": "comments", "values": [{"value": 12}]},
                                             {"name": "shares", "values": [{"value": 30}]}, {"name": "saved", "values": [{"value": 25}]},
                                             {"name": "ig_reels_avg_watch_time", "values": [{"value": 9000}]}]})
                if p.path.endswith("/MEDIA1"):
                    return s._send({"permalink": "https://instagram.com/reel/TEST123/"})
                s._send({"followers_count": 321})
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    @property
    def base(self):
        return f"http://127.0.0.1:{self.port}/v1"

    def stop(self):
        self.srv.shutdown()
