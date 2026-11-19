"""Performance tracking and learning. Only real data: Instagram Graph API insights (your own account, your token)
or numbers you import. Nothing is estimated unless clearly labelled. Small samples => low confidence, shown as such."""
from __future__ import annotations
import csv, io, json, os, statistics, time, datetime as dt
from . import config, db, util

METRIC_KEYS = ["views", "reach", "likes", "comments", "shares", "saves", "avg_watch_s", "total_watch_s", "completion_rate", "followers_gained"]
IG_METRICS = {"views": "views", "reach": "reach", "likes": "likes", "comments": "comments", "shares": "shares", "saved": "saves",
              "ig_reels_avg_watch_time": "avg_watch_s", "ig_reels_video_view_total_time": "total_watch_s", "follows": "followers_gained"}


def record(video_id: str, source: str, metrics: dict):
    db.x("INSERT INTO metrics(video_id,fetched_at,source,metrics) VALUES(?,?,?,?)", (video_id, time.time(), source, json.dumps(metrics)))


def latest(video_id: str) -> dict | None:
    r = db.q("SELECT * FROM metrics WHERE video_id=? ORDER BY fetched_at DESC, id DESC LIMIT 1", (video_id,))
    return {**json.loads(r[0]["metrics"]), "_source": r[0]["source"], "_at": r[0]["fetched_at"]} if r else None


def engagement(m: dict) -> float | None:
    """Weighted interactions per 100 viewers reached. Shares/saves count most: they are the strongest 'worth sending' signals."""
    reach = m.get("reach") or m.get("views")
    if not reach:
        return None
    return round(100 * (3 * (m.get("shares") or 0) + 3 * (m.get("saves") or 0) + 2 * (m.get("comments") or 0) + (m.get("likes") or 0)) / reach, 2)


def import_csv(text: str) -> dict:
    """Columns (header row, any order): id|video_id|permalink|title, views, reach, likes, comments, shares, saves, avg_watch_s, completion_rate (0-1 or %), followers_gained."""
    rows = list(csv.DictReader(io.StringIO(text.strip())))
    ok, miss = 0, []
    vids = db.q("SELECT id,title,permalink,ig_media_id FROM videos")
    for r in rows:
        r = {k.strip().lower(): (v or "").strip() for k, v in r.items() if k}
        key = r.get("video_id") or r.get("id") or r.get("permalink") or r.get("title") or ""
        v = next((x for x in vids if key and key in (x["id"], x["permalink"], x["ig_media_id"], x["title"])), None)
        if not v:
            miss.append(key); continue
        m = {}
        for k in METRIC_KEYS:
            if r.get(k):
                try:
                    m[k] = float(r[k].replace(",", "").rstrip("%"))
                except ValueError:
                    pass
        if "completion_rate" in m and m["completion_rate"] > 1:
            m["completion_rate"] /= 100
        record(v["id"], "import", m); ok += 1
    return {"imported": ok, "unmatched": miss}


def _graph(settings, path, params=None):
    ig = settings["instagram"]
    token = os.environ.get(ig["token_env"], "")
    if not token:
        raise RuntimeError(f"${ig['token_env']} is not set")
    import urllib.parse
    qs = urllib.parse.urlencode({**(params or {}), "access_token": token})
    return util.fetch_json(f"{ig['graph_base']}/{path}?{qs}")


def sync_instagram(settings=None) -> dict:
    s = settings or config.load()
    done, errs = 0, []
    for v in db.q("SELECT id,ig_media_id FROM videos WHERE ig_media_id IS NOT NULL AND ig_media_id!=''"):
        got = {}
        try:
            d = _graph(s, f"{v['ig_media_id']}/insights", {"metric": ",".join(IG_METRICS)})
            for e in d.get("data", []):
                got[IG_METRICS[e["name"]]] = e["values"][0]["value"]
        except Exception:
            for ig_name, ours in IG_METRICS.items():     # some metrics are unavailable for some media; ask one by one
                try:
                    d = _graph(s, f"{v['ig_media_id']}/insights", {"metric": ig_name})
                    got[ours] = d["data"][0]["values"][0]["value"]
                except Exception as e:
                    errs.append(f"{v['id']}:{ig_name}: {str(e)[:60]}")
        if "avg_watch_s" in got and got["avg_watch_s"] > 1000:      # API reports ms
            got["avg_watch_s"] /= 1000
        if got.get("total_watch_s", 0) > 1e5:
            got["total_watch_s"] /= 1000
        if got:
            record(v["id"], "instagram_api", got); done += 1
    try:
        a = _graph(s, s["instagram"]["user_id"] or "me", {"fields": "followers_count"})
        record("_account", "instagram_api", {"followers": a.get("followers_count")})
    except Exception as e:
        errs.append(f"account: {str(e)[:80]}")
    return {"videos_synced": done, "errors": errs[:12]}


def table() -> list[dict]:
    out = []
    for v in db.q("SELECT * FROM videos WHERE status IN ('posted') OR ig_media_id IS NOT NULL ORDER BY posted_at DESC"):
        m = latest(v["id"]) or {}
        er = engagement(m) if m else None
        ret = (m["avg_watch_s"] / v["duration"]) if m.get("avg_watch_s") and v.get("duration") else m.get("completion_rate")
        out.append({"id": v["id"], "title": v["title"], "category": v["category"], "style": v["style"], "posted_at": v["posted_at"],
                    "permalink": v["permalink"], "duration": v["duration"], "metrics": {k: m.get(k) for k in METRIC_KEYS},
                    "engagement": er, "retention": round(ret, 3) if ret else None, "source": m.get("_source"), "metrics_at": m.get("_at")})
    return out


def learn(min_total=3, min_group=2, k=3.0) -> dict:
    """Shrinkage estimate of how each category/style performs vs. your own average. Returns multipliers in [0.85, 1.20]."""
    rows = [r for r in table() if r["engagement"] is not None]
    res = {"category": {}, "style": {}, "n": len(rows), "means": {}}
    if len(rows) < min_total:
        res["note"] = f"need >= {min_total} posted videos with metrics before anything is learned (have {len(rows)})"
        return res
    allm = statistics.mean(r["engagement"] for r in rows)
    res["means"]["all"] = round(allm, 2)
    for dim in ("category", "style"):
        groups = {}
        for r in rows:
            groups.setdefault(r[dim], []).append(r["engagement"])
        for g, vals in groups.items():
            if len(vals) < min_group or allm <= 0:
                continue
            shrunk = (sum(vals) + k * allm) / (len(vals) + k)
            res[dim][g] = round(max(0.85, min(1.20, shrunk / allm)), 3)
            res["means"][f"{dim}:{g}"] = {"n": len(vals), "mean": round(statistics.mean(vals), 2)}
    res["confidence"] = "low" if len(rows) < 10 else "medium" if len(rows) < 30 else "reasonable"
    return res


def best_hours(min_posts=6) -> dict | None:
    rows = [(dt.datetime.fromtimestamp(r["posted_at"]).hour, r["engagement"]) for r in table() if r["posted_at"] and r["engagement"] is not None]
    if len(rows) < min_posts:
        return None
    by = {}
    for h, e in rows:
        by.setdefault(h, []).append(e)
    best = sorted(((statistics.mean(v), h, len(v)) for h, v in by.items() if len(v) >= 2), reverse=True)
    return {"hour": best[0][1], "n": best[0][2], "mean": round(best[0][0], 2)} if best else None


def recommend_time(settings=None, now: float | None = None) -> dict:
    s = settings or config.load()
    now_dt = dt.datetime.fromtimestamp(now or time.time())
    bh = best_hours()
    hours = [bh["hour"]] if bh else s["posting_hours_local"]
    why = (f"your own posts at {bh['hour']}:00 averaged the highest engagement (n={bh['n']}; small sample)" if bh
           else "default slots (no performance history yet); will adapt once you have enough posted videos with metrics")
    for day in (0, 1):
        for h in sorted(hours):
            c = (now_dt + dt.timedelta(days=day)).replace(hour=h, minute=5, second=0, microsecond=0)
            if c > now_dt + dt.timedelta(minutes=30):
                return {"at": c.timestamp(), "iso": c.isoformat(timespec="minutes"), "why": why}
    c = now_dt + dt.timedelta(days=1)
    return {"at": c.timestamp(), "iso": c.isoformat(timespec="minutes"), "why": why}
