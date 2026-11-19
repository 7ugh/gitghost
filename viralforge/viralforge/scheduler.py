"""`tick` is meant to run every few minutes (launchd/cron). It (1) publishes approved videos whose scheduled time has arrived and
(2) runs the daily research+generation job at most once per calendar day. It never approves anything and never publishes unapproved work."""
from __future__ import annotations
import datetime as dt, json, os, sys, time
from . import config, db, publish


def _marker():
    return config.HOME / "last_daily.json"


def daily_due(s, now=None) -> tuple[bool, str]:
    now = now or dt.datetime.now()
    d = s["daily"]
    if not d["enabled"]:
        return False, "daily automation disabled"
    hh, mm = map(int, d["time"].split(":"))
    if now < now.replace(hour=hh, minute=mm, second=0, microsecond=0):
        return False, "not yet time"
    m = json.loads(_marker().read_text()) if _marker().exists() else {}
    if m.get("date") == now.date().isoformat():
        return False, "already ran today (never auto-regenerates after a rejection)"
    pending = db.q("SELECT COUNT(*) n FROM videos WHERE status='pending_approval'")[0]["n"]
    if pending >= 3:
        return False, f"{pending} videos already waiting for your approval"
    return True, "due"


def tick(now=None, progress=None) -> dict:
    s = config.load()
    out = {"published": [], "errors": [], "daily": None}
    for v in db.q("SELECT id,scheduled_at FROM videos WHERE status='scheduled' AND scheduled_at<=?", (time.time(),)):
        try:
            if not publish.is_approved(v["id"]):
                db.update_video(v["id"], status="pending_approval"); continue
            r = publish.publish_instagram(v["id"], s)
            out["published"].append({"id": v["id"], **r})
        except publish.PublishUnavailable as e:
            db.update_video(v["id"], status="approved", scheduled_at=None)
            db.log_event(v["id"], "schedule_needs_manual", f"due but cannot publish via API: {e}")
            out["errors"].append({"id": v["id"], "error": f"manual publish needed: {e}"})
        except Exception as e:
            out["errors"].append({"id": v["id"], "error": str(e)[:200]})
    due, why = daily_due(s, now)
    if due:
        from . import pipeline
        _marker().write_text(json.dumps({"date": dt.date.today().isoformat(), "at": time.time()}))   # mark first: a crash must not loop
        try:
            out["daily"] = pipeline.run_daily(s, progress=progress)
        except Exception as e:
            out["daily"] = {"ok": False, "reason": str(e)[:300]}
    else:
        out["daily"] = {"skipped": why}
    return out


def install_text() -> str:
    py = sys.executable
    home = config.HOME
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
 <key>Label</key><string>com.viralforge.tick</string>
 <key>ProgramArguments</key><array><string>{py}</string><string>-m</string><string>viralforge</string><string>tick</string></array>
 <key>WorkingDirectory</key><string>{os.getcwd()}</string>
 <key>EnvironmentVariables</key><dict><key>VIRALFORGE_HOME</key><string>{home}</string></dict>
 <key>StartInterval</key><integer>600</integer>
 <key>StandardOutPath</key><string>{home}/tick.log</string><key>StandardErrorPath</key><string>{home}/tick.log</string>
</dict></plist>"""
    return (f"# macOS (recommended): save as ~/Library/LaunchAgents/com.viralforge.tick.plist then run:\n#   launchctl load ~/Library/LaunchAgents/com.viralforge.tick.plist\n"
            f"{plist}\n\n# or cron (crontab -e):\n*/10 * * * * cd {os.getcwd()} && VIRALFORGE_HOME={home} {py} -m viralforge tick >> {home}/tick.log 2>&1\n"
            "\n# The Mac must be awake at the scheduled time (System Settings > Battery > Options > 'Prevent automatic sleeping' or use `caffeinate`).")
