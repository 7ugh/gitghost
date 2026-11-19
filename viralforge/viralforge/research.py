"""Trend research from public/authorized sources. Every source reports ok/error; nothing is fabricated.

Not scraped (no sanctioned public API; scraping violates their terms): Instagram Reels, TikTok.
Use the dashboard's "Add observation" (manual signals) for what you see there, or YouTube Shorts via the
official YouTube Data API (free quota).
"""
from __future__ import annotations
import json, os, re, time, datetime as dt, xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from . import config, db, util

SUB_CATEGORY = {
    "todayilearned": "facts", "interestingasfuck": "facts", "mildlyinteresting": "facts", "showerthoughts": "what-if",
    "technology": "technology", "gadgets": "technology", "gaming": "gaming", "roblox": "roblox", "space": "educational",
    "science": "educational", "unresolvedmysteries": "mysteries", "funny": "comedy", "nostupidquestions": "internet trends",
    "askreddit": "storytelling", "tifu": "storytelling", "videos": "internet trends", "nextfuckinglevel": "challenges",
}
CATEGORY_KEYWORDS = {
    "roblox": ["roblox", "robux", "blox fruits", "adopt me", "brookhaven"],
    "gaming": ["game", "gaming", "minecraft", "fortnite", "playstation", "xbox", "nintendo", "steam", "esports", "zelda", "gta", "valorant", "switch 2"],
    "technology": ["ai ", " ai", "chip", "iphone", "android", "software", "robot", "startup", "openai", "google", "apple", "tesla", "cyber", "quantum", "battery", "satellite", "gpu"],
    "mysteries": ["mystery", "unsolved", "disappear", "unexplained", "strange", "ghost", "cryptid", "cold case", "ancient", "lost city", "shipwreck"],
    "what-if": ["what if", "imagine", "would happen", "hypothetical"],
    "comedy": ["funny", "meme", "joke", "hilarious", "prank", "parody", "lol"],
    "challenges": ["challenge", "tried", "24 hours", "for a week", "record attempt"],
    "educational": ["why ", "how does", "science", "explained", "history", "physics", "biology", "space", "planet", "brain", "ocean"],
    "facts": ["fact", "til ", "turns out", "actually", "discovered", "scientists", "study finds", "researchers"],
    "storytelling": ["story", "true story", "happened to", "once", "my "],
    "animated stories": ["animated", "animation", "cartoon"],
    "internet trends": ["viral", "trend", "tiktok", "instagram", "youtube", "trending", "went viral", "creator", "influencer"],
}


def classify(title: str, hint: str | None = None) -> str:
    if hint:
        return hint
    t = f" {title.lower()} "
    best, bs = "facts", 0
    for cat, kws in CATEGORY_KEYWORDS.items():
        s = sum(1 for k in kws if k in t)
        if s > bs:
            best, bs = cat, s
    return best


def _sig(source, title, url="", summary="", score=0.0, age_hours=24.0, category=None, **meta):
    return {"source": source, "title": re.sub(r"\s+", " ", title).strip(), "url": url, "summary": (summary or "")[:600],
            "score": float(score), "age_hours": float(age_hours), "category": classify(title, category), "meta": meta}


# ------------------------------------------------------------------ sources
def src_reddit(cfg, s):
    out = []
    for sub in cfg["subreddits"]:
        d = util.fetch_json(f"https://www.reddit.com/r/{sub}/top.json?t=day&limit=12&raw_json=1")
        for c in d["data"]["children"]:
            p = c["data"]
            if p.get("over_18") or p.get("stickied"):
                continue
            out.append(_sig(f"reddit/r/{sub}", p["title"], "https://reddit.com" + p["permalink"], p.get("selftext", ""),
                            p["ups"] + 2 * p["num_comments"], (time.time() - p["created_utc"]) / 3600,
                            SUB_CATEGORY.get(sub.lower()), comments=p["num_comments"], ups=p["ups"]))
        time.sleep(1.0)  # be polite
    return out


def src_hackernews(cfg, s):
    ids = util.fetch_json("https://hacker-news.firebaseio.com/v0/topstories.json")[:25]
    def one(i):
        try:
            return util.fetch_json(f"https://hacker-news.firebaseio.com/v0/item/{i}.json")
        except Exception:
            return None
    with ThreadPoolExecutor(6) as ex:
        items = [i for i in ex.map(one, ids) if i and i.get("title")]
    return [_sig("hackernews", i["title"], i.get("url") or f"https://news.ycombinator.com/item?id={i['id']}", "",
                 i.get("score", 0) + 2 * i.get("descendants", 0), (time.time() - i["time"]) / 3600, "technology") for i in items]


def src_google_trends(cfg, s):
    raw = util.fetch(f"https://trends.google.com/trending/rss?geo={cfg.get('geo','US')}")
    root = ET.fromstring(raw)
    ns = {"ht": "https://trends.google.com/trending/rss"}
    out = []
    for it in root.iter("item"):
        title = it.findtext("title") or ""
        traffic = (it.findtext("ht:approx_traffic", namespaces=ns) or "0").replace("+", "").replace(",", "")
        news = [n.findtext("ht:news_item_title", namespaces=ns) for n in it.findall("ht:news_item", ns)]
        out.append(_sig("google_trends", title, "", " | ".join(n for n in news if n), float(traffic or 0) / 100, 12, None, traffic=traffic))
    return out


def src_wikipedia(cfg, s):
    d = dt.date.today() - dt.timedelta(days=2)
    j = util.fetch_json(f"https://wikimedia.org/api/rest_v1/metrics/pageviews/top/en.wikipedia/all-access/{d:%Y/%m/%d}")
    out = []
    for a in j["items"][0]["articles"][:40]:
        t = a["article"].replace("_", " ")
        if t in ("Main Page",) or ":" in t or t.startswith("List of"):
            continue
        out.append(_sig("wikipedia/most-read", t, f"https://en.wikipedia.org/wiki/{a['article']}", "", a["views"] / 1000, 36, "educational"))
    return out


def _rss_items(raw: bytes):
    root = ET.fromstring(raw)
    for it in list(root.iter("item")) + list(root.iter("{http://www.w3.org/2005/Atom}entry")):
        title = it.findtext("title") or it.findtext("{http://www.w3.org/2005/Atom}title") or ""
        link = it.findtext("link") or ""
        if not link:
            le = it.find("{http://www.w3.org/2005/Atom}link")
            link = le.get("href") if le is not None else ""
        desc = re.sub(r"<[^>]+>", "", it.findtext("description") or it.findtext("{http://www.w3.org/2005/Atom}summary") or "")
        yield title, link, desc


def src_news_rss(cfg, s):
    out = []
    for feed in cfg["feeds"]:
        try:
            items = list(_rss_items(util.fetch(feed)))[:12]
        except Exception:
            continue
        for rank, (t, l, d) in enumerate(items):
            out.append(_sig("news/" + re.sub(r"^https?://(www\.)?|/.*$", "", feed), t, l, d, 100 - rank * 6, 8 + rank))
    if not out:
        raise RuntimeError("no feed reachable")
    return out


def src_youtube(cfg, s):
    out = []
    key = os.environ.get(cfg.get("api_key_env", "YOUTUBE_API_KEY"), "")
    for cid in cfg.get("channel_ids", []):
        for rank, (t, l, d) in enumerate(list(_rss_items(util.fetch(f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}")))[:6]):
            out.append(_sig(f"youtube/channel/{cid[:8]}", t, l, d, 60 - rank * 8, 24 + rank * 24))
    if key:
        j = util.fetch_json(f"https://www.googleapis.com/youtube/v3/videos?part=snippet,statistics&chart=mostPopular&regionCode={cfg.get('region','US')}&maxResults=25&key={key}")
        for v in j.get("items", []):
            st = v.get("statistics", {})
            out.append(_sig("youtube/mostPopular", v["snippet"]["title"], f"https://youtu.be/{v['id']}", v["snippet"].get("description", ""),
                            int(st.get("viewCount", 0)) / 5000 + int(st.get("commentCount", 0)) / 50, 24, None, views=st.get("viewCount")))
        for qy in cfg.get("shorts_queries", [])[:5]:   # 100 quota units each; free tier = 10,000/day
            after = (dt.datetime.utcnow() - dt.timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
            j = util.fetch_json("https://www.googleapis.com/youtube/v3/search?part=snippet&type=video&videoDuration=short&order=viewCount"
                                f"&maxResults=8&publishedAfter={after}&q={urllib_quote(qy + ' #shorts')}&key={key}")
            for rank, v in enumerate(j.get("items", [])):
                out.append(_sig("youtube/shorts", v["snippet"]["title"], f"https://youtu.be/{v['id']['videoId']}", v["snippet"].get("description", ""), 80 - rank * 6, 36))
    elif not out:
        raise RuntimeError(f"no ${cfg.get('api_key_env')} set and no channel_ids configured (YouTube Data API key is free)")
    return out


def urllib_quote(s):
    import urllib.parse
    return urllib.parse.quote(s)


SOURCES = {"reddit": src_reddit, "hackernews": src_hackernews, "google_trends": src_google_trends, "wikipedia": src_wikipedia,
           "news_rss": src_news_rss, "youtube": src_youtube}


def manual_signals(max_age_days=7):
    cutoff = time.time() - max_age_days * 86400
    rows = db.q("SELECT * FROM manual_signals WHERE created_at>?", (cutoff,))
    return [_sig(f"manual/{r['platform']}", r["title"], r["url"] or "", r["note"] or "", 150, (time.time() - r["created_at"]) / 3600, r["category"] or None) for r in rows]


def add_manual(platform: str, title: str, url: str = "", note: str = "", category: str = ""):
    return db.x("INSERT INTO manual_signals(created_at,platform,title,url,note,category) VALUES(?,?,?,?,?,?)",
                (time.time(), platform, title, url, note, category))


def run_research(settings: dict | None = None, fixture: str | None = None) -> dict:
    """Fetch all enabled sources. Stores signals; returns {run_id, status, count}."""
    s = settings or config.load()
    started = time.time()
    status, signals = {}, []
    if fixture:
        with open(fixture) as fh:
            data = json.load(fh)
        signals = data["signals"]
        for sg in signals:
            sg.setdefault("meta", {}); sg.setdefault("summary", ""); sg.setdefault("url", ""); sg.setdefault("age_hours", 12)
            sg["category"] = classify(sg["title"], sg.get("category"))
        status["fixture"] = {"ok": True, "count": len(signals), "message": "SAMPLE DATA from fixture file, not live research"}
    else:
        def work(name):
            cfg = s["sources"].get(name, {})
            if not cfg.get("enabled"):
                return name, None, "disabled"
            try:
                return name, SOURCES[name](cfg, s), None
            except Exception as e:
                return name, None, f"{type(e).__name__}: {str(e)[:160]}"
        with ThreadPoolExecutor(len(SOURCES)) as ex:
            for name, res, err in ex.map(work, SOURCES):
                if err == "disabled":
                    status[name] = {"ok": False, "count": 0, "message": "disabled"}
                elif err:
                    status[name] = {"ok": False, "count": 0, "message": err}
                else:
                    status[name] = {"ok": True, "count": len(res), "message": ""}
                    signals += res
        m = manual_signals()
        if m:
            signals += m
            status["manual"] = {"ok": True, "count": len(m), "message": "your own observations (e.g. TikTok/Instagram)"}
        status["tiktok_instagram"] = {"ok": False, "count": 0, "message": "no sanctioned public trend API; use manual observations"}
    run_id = db.x("INSERT INTO research_runs(started_at,finished_at,source_status) VALUES(?,?,?)", (started, time.time(), json.dumps(status)))
    for sg in signals:
        db.x("INSERT INTO signals(run_id,source,title,url,summary,score,age_hours,category,fetched_at,meta) VALUES(?,?,?,?,?,?,?,?,?,?)",
             (run_id, sg["source"], sg["title"], sg["url"], sg["summary"], sg["score"], sg["age_hours"], sg["category"], time.time(), json.dumps(sg["meta"])))
    return {"run_id": run_id, "status": status, "count": len(signals)}


def latest_signals(run_id: int | None = None):
    if run_id is None:
        r = db.q("SELECT id FROM research_runs ORDER BY id DESC LIMIT 1")
        if not r:
            return []
        run_id = r[0]["id"]
    return db.q("SELECT * FROM signals WHERE run_id=? ORDER BY score DESC", (run_id,))
