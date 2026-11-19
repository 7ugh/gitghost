import json, threading, time, unittest, urllib.request, urllib.error
from viralforge import server, db, config, publish
from .helpers import make_video, NOTES


class ApiBase(unittest.TestCase):
    local = True

    @classmethod
    def setUpClass(cls):
        cls.srv = server.make_server("127.0.0.1", 0)
        if not cls.local:
            cls.srv.local_only = False
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def call(self, path, body=None, headers=None, raw=False, method=None):
        h = {"X-VF": "1", "Content-Type": "application/json", **(headers or {})}
        req = urllib.request.Request(self.base + path, json.dumps(body).encode() if body is not None else None, h, method=method)
        try:
            with urllib.request.urlopen(req) as r:
                return (r.status, r.read() if raw else json.loads(r.read()), dict(r.headers))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}"), dict(e.headers)

    def wait(self, jid, timeout=240):
        t = time.time()
        while time.time() - t < timeout:
            _, j, _ = self.call(f"/api/jobs/{jid}")
            if j["state"] in ("done", "error"):
                return j
            time.sleep(0.5)
        self.fail("job timeout")


class Api(ApiBase):
    def test_static_and_status(self):
        c, body, _ = self.call("/", raw=True)
        self.assertEqual(c, 200); self.assertIn(b"ViralForge", body)
        c, st, _ = self.call("/api/status")
        self.assertEqual(c, 200); self.assertIn("llm", st); self.assertIn("instagram", st)

    def test_research_ideas_flow_with_sample_data(self):
        c, r, _ = self.call("/api/research/run", {"sample": True}); self.assertEqual(self.wait(r["job"])["state"], "done")
        c, r, _ = self.call("/api/research"); self.assertIn("SAMPLE", r["run"]["source_status"]["fixture"]["message"])
        c, i, _ = self.call("/api/ideas"); self.assertGreater(len(i["ideas"]), 3); self.assertIn("cannot promise views", i["disclaimer"])

    def test_csrf_header_required(self):
        req = urllib.request.Request(self.base + "/api/settings", b"{}", {"Content-Type": "application/json"})
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req)
        self.assertEqual(e.exception.code, 403)

    def test_settings_never_expose_token(self):
        c, s, _ = self.call("/api/settings", {"daily": {"enabled": True, "time": "08:30"}})
        self.assertEqual(s["server"]["token"], "(hidden)"); self.assertEqual(s["daily"]["time"], "08:30")
        self.call("/api/settings", {"daily": {"enabled": False}})

    def test_full_approval_workflow(self):
        vid = make_video(topic="api flow " + str(time.time()))
        # cannot schedule / post before approval
        c, e, _ = self.call(f"/api/videos/{vid}/schedule", {"at": time.time() + 3600}); self.assertEqual(c, 403)
        c, e, _ = self.call(f"/api/videos/{vid}/post_now", {"confirm": True}); self.assertEqual(c, 403)
        self.assertEqual(db.get_video(vid)["status"], "pending_approval")
        c, v, _ = self.call(f"/api/videos/{vid}"); self.assertFalse(v["approved"]); self.assertIn("project", v)
        # approve -> schedule
        self.assertEqual(self.call(f"/api/videos/{vid}/approve", {})[0], 200)
        c, s, _ = self.call(f"/api/videos/{vid}/schedule", {"at": time.time() + 3600})
        self.assertEqual(c, 200); self.assertFalse(s["auto_publish"]); self.assertIn("manually", s["note"])
        self.assertEqual(db.get_video(vid)["status"], "scheduled")
        # post_now needs explicit confirm; without IG credentials falls back to manual package
        c, e, _ = self.call(f"/api/videos/{vid}/post_now", {}); self.assertEqual(c, 400)
        c, p, _ = self.call(f"/api/videos/{vid}/post_now", {"confirm": True})
        self.assertTrue(p["manual"]); self.assertEqual(self.call(p["package"], raw=True)[0], 200)
        self.assertEqual(db.get_video(vid)["status"], "scheduled")          # manual fallback did NOT mark it posted
        c, _, _ = self.call(f"/api/videos/{vid}/manual_posted", {"permalink": "https://instagram.com/reel/x"}); self.assertEqual(c, 200)
        self.assertEqual(db.get_video(vid)["status"], "posted")

    def test_reject_does_not_regenerate(self):
        vid = make_video(topic="reject flow " + str(time.time()))
        n = db.q("SELECT COUNT(*) n FROM videos")[0]["n"]
        self.assertEqual(self.call(f"/api/videos/{vid}/reject", {"reason": "boring"})[0], 200)
        time.sleep(1)
        self.assertEqual(db.q("SELECT COUNT(*) n FROM videos")[0]["n"], n)
        self.assertEqual(db.get_video(vid)["status"], "rejected")
        self.assertEqual(self.call(f"/api/videos/{vid}/approve", {})[0], 409)       # rejected videos can't be approved directly

    def test_regenerate_with_feedback_keeps_gate_closed(self):
        vid = make_video(topic="regen flow " + str(time.time()))
        self.call(f"/api/videos/{vid}/approve", {})
        c, r, _ = self.call(f"/api/videos/{vid}/regenerate", {"feedback": "shorter"})
        j = self.wait(r["job"])
        self.assertEqual(j["state"], "done", j)
        v = db.get_video(vid)
        self.assertEqual(v["status"], "pending_approval"); self.assertEqual(v["feedback"], "shorter")
        self.assertFalse(publish.is_approved(vid))

    def test_range_requests_for_mobile_preview(self):
        vid = make_video(topic="range flow " + str(time.time()))
        req = urllib.request.Request(self.base + f"/media/{vid}/final.mp4", headers={"Range": "bytes=0-99"})
        with urllib.request.urlopen(req) as r:
            self.assertEqual(r.status, 206); self.assertEqual(len(r.read()), 100); self.assertIn("bytes 0-99/", r.headers["Content-Range"])
        c, _, _ = self.call(f"/media/{vid}/../../settings.json", raw=True); self.assertIn(c, (404, 400))

    def test_create_from_pasted_notes_via_api(self):
        notes = "An octopus has three hearts: two pump blood past the gills, and a third pumps it around the rest of the body.\nOctopus blood is blue because it carries oxygen with a copper-based protein called hemocyanin instead of iron.\nThe heart that feeds the body stops beating while an octopus swims, which is one reason they prefer to crawl."
        c, r, _ = self.call("/api/videos/create", {"topic": "Octopus hearts via API", "notes_text": notes, "category": "facts"})
        j = self.wait(r["job"]); self.assertEqual(j["state"], "done", j)
        v = db.get_video(j["result"]); self.assertEqual(v["status"], "pending_approval")


class RemoteAuth(ApiBase):
    local = False

    def test_token_required_when_not_local(self):
        c, _, _ = self.call("/api/status"); self.assertEqual(c, 401)
        tok = config.load()["server"]["token"]
        c, _, h = self.call(f"/api/status?token={tok}"); self.assertEqual(c, 200)
        c, _, _ = self.call("/api/status", headers={"Cookie": f"vf_token={tok}"}); self.assertEqual(c, 200)
        c, _, _ = self.call("/api/status", headers={"Cookie": "vf_token=wrong"}); self.assertEqual(c, 401)


if __name__ == "__main__":
    unittest.main()
