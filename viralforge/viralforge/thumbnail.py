from __future__ import annotations
import pathlib, re
from .render import Renderer


def short_title(script: dict) -> str:
    t = script.get("cover_title") or script["title"]
    t = re.sub(r"\s+", " ", t).strip().upper()
    words = t.split()
    return " ".join(words[:6])


def make(renderer: Renderer, script: dict, vdir: pathlib.Path, t: float | None = None, title: str | None = None) -> str:
    t = 1.6 if t is None else t
    im = renderer.cover(title or short_title(script), min(t, renderer.total - 0.1))
    p = vdir / "thumb.jpg"
    im.save(p, quality=92)
    return str(p)
