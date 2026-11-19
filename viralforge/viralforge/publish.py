"""Publishing. HARD GATE: nothing is sent anywhere unless the most recent decision on the video is an explicit 'approved' event
(any edit/regenerate voids it). Instagram publishing uses the official Instagram Graph API with YOUR token; if unavailable,
a manual package (mp4 + cover + caption) is produced instead.

NOTE: the Graph API calls below follow Meta's documented Reels flow (create container -> upload -> poll -> media_publish) but could not
be exercised against Meta's servers from the dev sandbox; they are tested against a local mock only. Do a first post with a test video.
"""
from __future__ import annotations
import json, os, pathlib, time, urllib.parse, urllib.request, urllib.error, zipfile
from . import config, db, util

VOIDING = ("edited", "regenerated", "rejected", "ready_for_approval", "unapproved")


class NotApproved(Exception):
    pass


class PublishUnavailable(Exception):
    pass


def is_approved(vid: str) -> bool:
    ev = db.q("SELECT kind FROM events WHERE video_id=? AND kind IN ('approved','edited','regenerated','rejected','ready_for_approval','unapproved') ORDER BY id DESC LIMIT 1", (vid,))
    v = db.get_video(vid)
    return bool(ev and ev[0]["kind"] == "approved" and v and v["status"] in ("approved", "scheduled", "publishing"))


def require_approved(vid: str):
    if not is_approved(vid):
        raise NotApproved(f"video {vid} has not been explicitly approved (or was changed after approval)")


def _post(url, fields=None, headers=None, body=None, timeout=300):
    data = body if body is not None else urllib.parse.urlencode(fields or {}).encode()
    h = {"User-Agent": util.UA, **(headers or {})}
    if body is None:
        h["Content-Type"] = "application/x-www-form-urlencoded"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data, h, method="POST"), timeout=timeout) as r:
            txt = r.read().decode() or "{}"
            return json.loads(txt)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} from {url.split('?')[0]}: {e.read().decode()[:300]}")


def ig_ready(settings) -> tuple[bool, str]:
    ig = settings["instagram"]
    if not ig["user_id"]:
        return False, "Instagram user id not set (Settings)"
    if not os.environ.get(ig["token_env"]):
        return False, f"${ig['token_env']} not set (put it in {config.HOME}/.env)"
    if ig["mode"] == "video_url" and not ig["public_base_url"]:
        return False, "mode=video_url needs public_base_url"
    return True, "ok"


def publish_instagram(vid: str, settings=None, public_url: str | None = None, progress=None) -> dict:
    s = settings or config.load()
    require_approved(vid)                                     # <- the gate
    ok, why = ig_ready(s)
    if not ok:
        raise PublishUnavailable(why)
    ig = s["instagram"]; token = os.environ[ig["token_env"]]
    v = db.get_video(vid); vdir = config.video_dir(vid); mp4 = vdir / "final.mp4"
    caption = v["meta"]["description"]["caption"]
    P = progress or (lambda *a: None)
    db.update_video(vid, status="publishing"); db.log_event(vid, "publishing", ig["mode"])
    try:
        base = ig["graph_base"].rstrip("/")
        fields = {"media_type": "REELS", "caption": caption, "share_to_feed": "true", "access_token": token,
                  "thumb_offset": str(v["meta"].get("thumb_offset_ms", 0))}
        if ig["mode"] == "video_url":
            fields["video_url"] = public_url
            c = _post(f"{base}/{ig['user_id']}/media", fields)
        else:
            fields["upload_type"] = "resumable"
            c = _post(f"{base}/{ig['user_id']}/media", fields)
            P("uploading")
            up = c.get("uri") or f"{ig['rupload_base'].rstrip('/')}/{c['id']}"
            _post(up, headers={"Authorization": f"OAuth {token}", "offset": "0", "file_size": str(mp4.stat().st_size)}, body=mp4.read_bytes())
        cid = c["id"]
        P("processing")
        for _ in range(120):                                  # up to ~10 min
            st = util.fetch_json(f"{base}/{cid}?fields=status_code&access_token={urllib.parse.quote(token)}")
            if st.get("status_code") == "FINISHED":
                break
            if st.get("status_code") in ("ERROR", "EXPIRED"):
                raise RuntimeError(f"Instagram processing failed: {st}")
            time.sleep(5 if not os.environ.get("VF_FAST_POLL") else 0.05)
        else:
            raise RuntimeError("Instagram processing timed out")
        require_approved_during_publish(vid)
        pub = _post(f"{base}/{ig['user_id']}/media_publish", {"creation_id": cid, "access_token": token})
        media_id = pub["id"]
        try:
            link = util.fetch_json(f"{base}/{media_id}?fields=permalink&access_token={urllib.parse.quote(token)}").get("permalink", "")
        except Exception:
            link = ""
        db.update_video(vid, status="posted", posted_at=time.time(), ig_media_id=media_id, permalink=link)
        db.log_event(vid, "posted", link or media_id)
        return {"media_id": media_id, "permalink": link}
    except Exception as e:
        db.update_video(vid, status="approved", meta={**v["meta"], "publish_error": str(e)[:300]})
        db.log_event(vid, "publish_failed", str(e)[:300])
        raise


def require_approved_during_publish(vid):
    v = db.get_video(vid)
    ev = db.q("SELECT kind FROM events WHERE video_id=? AND kind IN ('approved','edited','regenerated','rejected','unapproved') ORDER BY id DESC LIMIT 1", (vid,))
    if not ev or ev[0]["kind"] != "approved":
        raise NotApproved("approval was withdrawn while publishing")


def manual_package(vid: str) -> str:
    """Zip with the mp4, cover and caption for posting by hand from your phone/Mac (works with no API access at all)."""
    v = db.get_video(vid); vdir = config.video_dir(vid)
    z = vdir / "manual_package.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(vdir / "final.mp4", "video.mp4")
        zf.write(vdir / "thumb.jpg", "cover.jpg")
        zf.writestr("caption.txt", v["meta"]["description"]["caption"])
        zf.writestr("README.txt", "Instagram app -> + -> Reel -> pick video.mp4 -> Cover: cover.jpg -> paste caption.txt -> "
                                  "turn on 'AI label' if prompted -> Share.\nThen mark the video as posted in ViralForge.\n")
    return str(z)


def mark_posted(vid: str, permalink: str = ""):
    require_approved(vid)
    db.update_video(vid, status="posted", posted_at=time.time(), permalink=permalink)
    db.log_event(vid, "posted_manually", permalink)
