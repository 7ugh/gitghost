import json, os, time, unittest, zipfile
from unittest import mock
from PIL import Image
from viralforge import pipeline, publish, db, config, util, scheduler, analytics, render
from .helpers import make_video, MockGraph

_state = {}


def video():
    if "vid" not in _state:
        _state["vid"] = make_video()
    return _state["vid"]


class RenderOutput(unittest.TestCase):
    def test_mp4_specs_and_qc(self):
        vid = video(); d = config.video_dir(vid)
        info = util.ffprobe(str(d / "final.mp4"))
        v = next(s for s in info["streams"] if s["codec_type"] == "video")
        self.assertEqual((v["width"], v["height"], v["r_frame_rate"], v["codec_name"], v["pix_fmt"]), (1080, 1920, "30/1", "h264", "yuv420p"))
        self.assertTrue(any(s["codec_type"] == "audio" for s in info["streams"]))
        rec = db.get_video(vid)
        self.assertEqual(rec["status"], "pending_approval")                       # always ends awaiting approval
        by = {c["check"]: c["status"] for c in rec["qc"]["checks"]}
        for k in ("resolution", "frame rate", "codec", "loudness", "black frames", "caption readability", "caption sync", "opening hook", "description"):
            self.assertEqual(by[k], "pass", k)
        self.assertNotEqual(rec["qc"]["overall"], "fail")

    def test_thumbnail_and_srt_and_description(self):
        d = config.video_dir(video())
        self.assertEqual(Image.open(d / "thumb.jpg").size, (1080, 1920))
        self.assertIn("-->", (d / "captions.srt").read_text())
        self.assertIn("Sources:", db.get_video(video())["meta"]["description"]["caption"])

    def test_captions_cover_narration_in_order(self):
        p = pipeline.load_project(video())
        words = [w["w"] for c in p["captions"] for w in c["words"]]
        self.assertEqual(" ".join(words), " ".join(s["narration"] for s in p["script"]["scenes"]))
        starts = [c["start"] for c in p["captions"]]
        self.assertEqual(starts, sorted(starts))
        self.assertTrue(all(0 <= c["start"] < c["end"] <= p["timeline"]["total"] + 0.5 for c in p["captions"]))

    def test_qc_catches_wrong_resolution(self):
        import subprocess, tempfile
        bad = tempfile.mktemp(suffix=".mp4")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=s=640x360:d=2:r=24", "-f", "lavfi", "-i", "sine=d=2", "-shortest", bad], check=True)
        from viralforge import qc
        r = qc.run(bad, pipeline.load_project(video()))
        self.assertEqual(r["overall"], "fail"); self.assertEqual(next(c for c in r["checks"] if c["check"] == "resolution")["status"], "fail")

    def test_clip_scene_uses_authorized_footage(self):
        import subprocess, tempfile, pathlib
        clip = tempfile.mktemp(suffix=".mp4")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=s=1280x720:d=3:r=30", clip], check=True)
        p = pipeline.load_project(video())
        p["script"]["scenes"][1]["clip"] = clip
        r = render.Renderer(p["script"], p["timeline"], p["captions"], config.video_dir(video()), fps=30)
        try:
            frame = r.frame(p["timeline"]["scenes"][1]["start"] + 0.5)
            self.assertEqual(frame.shape, (1920, 1080, 3))
            self.assertGreater(frame.std(), 20)       # real footage content, not a flat background
        finally:
            r.close()
        from viralforge import qc
        p["assets"] = [{"scene": 1, "path": clip, "license": ""}]
        self.assertEqual(next(c for c in qc.run(str(config.video_dir(video()) / "final.mp4"), p)["checks"] if c["check"] == "asset licences")["status"], "fail")


class ApprovalGate(unittest.TestCase):
    def setUp(self):
        self.vid = make_video(topic="Gate test " + str(time.time()))
        self.mock = MockGraph()
        os.environ["IG_TEST_TOKEN"] = "TOKEN123"
        s = config.load(); s["instagram"].update({"user_id": "17841", "token_env": "IG_TEST_TOKEN", "graph_base": self.mock.base, "mode": "resumable"})
        self.s = s

    def tearDown(self):
        self.mock.stop()

    def test_unapproved_never_publishes(self):
        with self.assertRaises(publish.NotApproved):
            publish.publish_instagram(self.vid, self.s)
        self.assertEqual(self.mock.log, [])                 # not a single request left the building

    def test_edit_voids_approval(self):
        db.update_video(self.vid, status="approved"); db.log_event(self.vid, "approved")
        self.assertTrue(publish.is_approved(self.vid))
        pipeline.apply_edit(self.vid, {"description": "Edited caption #test"}, self.s)
        self.assertFalse(publish.is_approved(self.vid))
        with self.assertRaises(publish.NotApproved):
            publish.publish_instagram(self.vid, self.s)

    def test_publish_flow_against_mock(self):
        db.update_video(self.vid, status="approved"); db.log_event(self.vid, "approved")
        r = publish.publish_instagram(self.vid, self.s)
        self.assertEqual(r, {"media_id": "MEDIA1", "permalink": "https://instagram.com/reel/TEST123/"})
        v = db.get_video(self.vid)
        self.assertEqual((v["status"], v["ig_media_id"]), ("posted", "MEDIA1"))
        posts = [e for e in self.mock.log if e["method"] == "POST"]
        self.assertEqual([p["path"].split("/")[-1] for p in posts], ["media", "CONT1", "media_publish"])
        create = posts[0]["form"]
        self.assertEqual((create["media_type"], create["upload_type"], create["access_token"]), ("REELS", "resumable", "TOKEN123"))
        self.assertIn("Made with AI", create["caption"])
        up = posts[1]
        self.assertEqual(up["headers"]["authorization"], "OAuth TOKEN123"); self.assertEqual(up["bytes"], int(up["headers"]["file_size"]))

    def test_processing_failure_returns_to_approved_not_posted(self):
        self.mock.fail = True
        db.update_video(self.vid, status="approved"); db.log_event(self.vid, "approved")
        with self.assertRaises(RuntimeError):
            publish.publish_instagram(self.vid, self.s)
        v = db.get_video(self.vid)
        self.assertEqual(v["status"], "approved"); self.assertIn("failed", v["meta"]["publish_error"])
        self.assertFalse([e for e in self.mock.log if e["path"].endswith("media_publish")])

    def test_unavailable_without_credentials(self):
        db.update_video(self.vid, status="approved"); db.log_event(self.vid, "approved")
        s = json.loads(json.dumps(self.s)); s["instagram"]["token_env"] = "NOPE_NOT_SET"
        with self.assertRaises(publish.PublishUnavailable):
            publish.publish_instagram(self.vid, s)

    def test_manual_package(self):
        db.update_video(self.vid, status="approved"); db.log_event(self.vid, "approved")
        z = publish.manual_package(self.vid)
        self.assertEqual(sorted(zipfile.ZipFile(z).namelist()), ["README.txt", "caption.txt", "cover.jpg", "video.mp4"])

    def test_analytics_sync_from_mock(self):
        db.update_video(self.vid, status="approved", ig_media_id="MEDIA1", posted_at=time.time())
        r = analytics.sync_instagram(self.s)
        self.assertEqual(r["videos_synced"], 1)
        m = analytics.latest(self.vid)
        self.assertEqual((m["reach"], m["shares"], m["saves"], m["avg_watch_s"]), (1000, 30, 25, 9.0))   # ms -> s


class Scheduler(unittest.TestCase):
    def test_tick_publishes_only_approved_due_videos(self):
        mock = MockGraph(); os.environ["IG_TEST_TOKEN"] = "T"
        s = config.load(); s["instagram"].update({"user_id": "1", "token_env": "IG_TEST_TOKEN", "graph_base": mock.base}); config.save(s)
        try:
            a = make_video(topic="sched approved " + str(time.time())); b = make_video(topic="sched unapproved " + str(time.time()))
            db.update_video(a, status="scheduled", scheduled_at=time.time() - 5); db.log_event(a, "approved")
            db.update_video(b, status="scheduled", scheduled_at=time.time() - 5)      # scheduled but never approved (tampered)
            out = scheduler.tick()
            self.assertEqual([p["id"] for p in out["published"]], [a])
            self.assertEqual(db.get_video(b)["status"], "pending_approval")
        finally:
            mock.stop(); s["instagram"].update({"user_id": "", "token_env": "IG_ACCESS_TOKEN", "graph_base": "https://graph.instagram.com/v23.0"}); config.save(s)

    def test_due_falls_back_to_manual_when_api_missing(self):
        a = make_video(topic="sched manual " + str(time.time()))
        db.update_video(a, status="scheduled", scheduled_at=time.time() - 5); db.log_event(a, "approved")
        out = scheduler.tick()
        self.assertTrue(any("manual publish needed" in e["error"] for e in out["errors"]))
        self.assertEqual(db.get_video(a)["status"], "approved")

    def test_daily_runs_once_per_day_and_respects_queue(self):
        import datetime as dt
        s = json.loads(json.dumps(config.load())); s["daily"] = {"enabled": True, "time": "00:00", "styles": "auto"}
        m = scheduler._marker(); m.unlink(missing_ok=True)
        db.x("DELETE FROM videos WHERE status='pending_approval'")
        self.assertEqual(scheduler.daily_due(s), (True, "due"))
        m.write_text(json.dumps({"date": dt.date.today().isoformat()}))
        self.assertIn("already ran", scheduler.daily_due(s)[1])
        m.unlink()
        for i in range(3): db.x("INSERT INTO videos(id,topic,status,created_at) VALUES(?,?,?,?)", (f"p{i}", "t", "pending_approval", time.time()))
        self.assertIn("waiting for your approval", scheduler.daily_due(s)[1])
        db.x("DELETE FROM videos WHERE id LIKE 'p_'")
        s["daily"]["enabled"] = False
        self.assertFalse(scheduler.daily_due(s)[0])


class EditFlow(unittest.TestCase):
    def test_caption_edit_rerenders_and_thumbnail_edit_is_quick(self):
        vid = make_video(topic="edit flow " + str(time.time()))
        p = pipeline.load_project(vid)
        r = pipeline.apply_edit(vid, {"thumb_title": "THREE HEARTS?!", "thumb_time": 2.0}, None)
        self.assertEqual(r["mode"], "metadata only")
        caps = [" ".join(w["w"] for w in c["words"]) for c in p["captions"]]; caps[0] = "OCTOPUSES HAVE 3"
        r = pipeline.apply_edit(vid, {"captions": caps}, None)
        self.assertEqual(r["mode"], "re-rendered")
        self.assertEqual(pipeline.load_project(vid)["captions"][0]["words"][-1]["w"], "3")
        self.assertEqual(db.get_video(vid)["status"], "pending_approval")


if __name__ == "__main__":
    unittest.main()
