"""CLI:  python -m viralforge <command>"""
from __future__ import annotations
import argparse, json, sys, time
from . import config


_last = None


def prog(stage, frac=None):
    global _last
    key = (stage, int((frac or 0) * 4))
    if key != _last:
        _last = key
        sys.stderr.write(f"  [{time.strftime('%H:%M:%S')}] {stage}" + (f" {int(frac*100)}%" if frac else "") + "\n")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="viralforge")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("hardware", help="check this machine + recommend local models")
    r = sub.add_parser("research", help="fetch trend signals"); r.add_argument("--fixture", help="use sample signals json (offline testing)")
    i = sub.add_parser("ideas", help="rank ideas from the latest research"); i.add_argument("--top", type=int, default=10)
    m = sub.add_parser("make", help="research a topic and create ONE video for approval")
    m.add_argument("--topic", required=True); m.add_argument("--style", default=None, choices=["explainer", "story", "what_if", "comedy"])
    m.add_argument("--notes", help="notes JSON you wrote/verified (otherwise Wikipedia is tried)"); m.add_argument("--voice"); m.add_argument("--fps", type=int)
    m.add_argument("--category")
    d = sub.add_parser("daily", help="one full daily cycle (never publishes)"); d.add_argument("--fixture"); d.add_argument("--notes"); d.add_argument("--dry", action="store_true")
    sv = sub.add_parser("serve", help="dashboard + API"); sv.add_argument("--host"); sv.add_argument("--port", type=int)
    sub.add_parser("tick", help="publish due scheduled posts (run from cron/launchd every few minutes) + daily job if due")
    sub.add_parser("sync", help="pull Instagram insights")
    sub.add_parser("voices", help="list narrators")
    sub.add_parser("selftest", help="run the full test-suite against a temporary data dir (needs ffmpeg; ~5 min)")
    sub.add_parser("install-schedule", help="print launchd/cron entries for daily automation")
    a = ap.parse_args(argv)

    if a.cmd == "hardware":
        from . import hardware; print(json.dumps(hardware.report(), indent=2))
    elif a.cmd == "selftest":
        import unittest, pathlib
        root = pathlib.Path(__file__).resolve().parent.parent
        os_chdir = __import__("os").chdir; os_chdir(root)
        res = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.discover("tests", top_level_dir="."))
        sys.exit(0 if res.wasSuccessful() else 1)
    elif a.cmd == "voices":
        from . import tts; [print(v["backend"], "|", v["id"], "|", v["name"]) for v in tts.list_voices()]
    elif a.cmd == "research":
        from . import research; res = research.run_research(fixture=a.fixture)
        print(json.dumps(res, indent=2))
    elif a.cmd == "ideas":
        from . import ideas as I
        out = I.generate()
        for x in out[:a.top]:
            print(f"{x['score']:5.1f}  [{x['category']:<15}] {x['topic'][:80]}\n       {x['why']}")
        if out:
            print("\nexcluded as sensitive:", out[0]["meta"]["excluded_sensitive"] or "none")
    elif a.cmd == "make":
        from . import pipeline
        vid = pipeline.create_video(topic=a.topic, style=a.style, notes_file=a.notes, voice_id=a.voice, progress=prog, fps=a.fps, category=a.category)
        print(f"\nREADY FOR YOUR APPROVAL: {config.video_dir(vid) / 'final.mp4'}\n  id: {vid}\n  open the dashboard:  python -m viralforge serve")
    elif a.cmd == "daily":
        from . import pipeline
        print(json.dumps(pipeline.run_daily(progress=prog, fixture=a.fixture, notes_file=a.notes, dry=a.dry), indent=2, default=str)[:3000])
    elif a.cmd == "serve":
        from . import server; server.serve(a.host, a.port)
    elif a.cmd == "tick":
        from . import scheduler; print(json.dumps(scheduler.tick(), indent=2, default=str))
    elif a.cmd == "sync":
        from . import analytics; print(json.dumps(analytics.sync_instagram(), indent=2))
    elif a.cmd == "install-schedule":
        from . import scheduler; print(scheduler.install_text())


if __name__ == "__main__":
    main()
