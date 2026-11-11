"""Render the static site for one day's puzzle."""

from __future__ import annotations

import base64
import datetime as dt
import json
import shutil
import urllib.parse
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from globepoch.catalog import Catalog, MapEntry

HERE = Path(__file__).parent
GAME_NAME = "Globepoch"
TAGLINE = "Date the map. One old map a day; guess the year it was made."
MAX_POINTS = 100
#: One point is lost for every full this-many years off, so 0 points at 1000+ years off.
YEARS_PER_POINT = 10
#: Guesses per puzzle; after each miss the player gets a hint.
MAX_GUESSES = 3


def _keystream(seed: int, length: int) -> bytes:
    """Tiny LCG shared with ``app.js`` — this only keeps the answer out of view-source."""
    x = seed & 0x7FFFFFFF
    out = bytearray()
    for _ in range(length):
        x = (x * 1103515245 + 12345) & 0x7FFFFFFF
        out.append((x >> 16) & 0xFF)
    return bytes(out)


def obfuscate(payload: dict, number: int) -> str:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    key = _keystream(number * 7919 + 17, len(raw))
    return base64.b64encode(bytes(a ^ b for a, b in zip(raw, key, strict=True))).decode()


def deobfuscate(blob: str, number: int) -> dict:
    data = base64.b64decode(blob)
    key = _keystream(number * 7919 + 17, len(data))
    return json.loads(bytes(a ^ b for a, b in zip(data, key, strict=True)).decode())


def answer_payload(entry: MapEntry) -> dict:
    return {
        "year": entry.year,
        "title": entry.title,
        "creator": entry.creator,
        "place": entry.place,
        "blurb": entry.blurb,
        "credit": entry.credit,
        "source": entry.source_page,
    }


def _environment() -> Environment:
    return Environment(
        loader=FileSystemLoader(HERE / "templates"),
        autoescape=select_autoescape(["html", "j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def render_page(
    page_dir: Path,
    catalog: Catalog,
    entry: MapEntry,
    day: dt.date,
    latest: dt.date,
    viewer: dict,
    site_url: str,
    *,
    base: str = "",
    preview_url: str | None = None,
) -> Path:
    """Write one puzzle's ``index.html`` into ``page_dir``.

    ``viewer`` describes the image: ``{"kind": "dzi", "source": <DZI JSON>}``, whose ``Url``
    is either absolute or relative to the site root.
    ``base`` is the relative path from ``page_dir`` back to the site root ("" or "../").
    """
    number = catalog.puzzle_number(day)
    latest_number = catalog.puzzle_number(latest)
    site_url = site_url.rstrip("/") + "/"
    viewer = json.loads(json.dumps(viewer))
    tiles_url = urllib.parse.urlsplit(viewer["source"]["Image"]["Url"])
    if not tiles_url.netloc:  # tiles hosted in the site itself
        viewer["source"]["Image"]["Url"] = base + viewer["source"]["Image"]["Url"]
    is_archive = number < latest_number
    puzzle = {
        "name": GAME_NAME,
        "number": number,
        "latest": latest_number,
        "date": day.isoformat(),
        "siteUrl": site_url,
        "shareUrl": f"{site_url}{number}/" if is_archive else site_url,
        "base": base,
        "minYear": catalog.min_year,
        "maxYear": latest.year,
        "maxPoints": MAX_POINTS,
        "yearsPerPoint": YEARS_PER_POINT,
        "maxGuesses": MAX_GUESSES,
        "viewer": viewer,
        "days": [
            {"n": catalog.puzzle_number(d), "date": d.isoformat()}
            for d in (catalog.epoch + dt.timedelta(days=i) for i in range(latest_number))
        ],
        "answer": obfuscate(answer_payload(entry), number),
    }
    html = (
        _environment()
        .get_template("index.html.j2")
        .render(
            name=GAME_NAME,
            tagline=TAGLINE,
            number=number,
            day=day,
            is_archive=is_archive,
            base=base,
            page_url=puzzle["shareUrl"],
            preview_url=preview_url,
            tiles_origin=f"{tiles_url.scheme}://{tiles_url.netloc}" if tiles_url.netloc else None,
            puzzle_json=json.dumps(puzzle, ensure_ascii=False).replace("</", "<\\/"),
        )
    )
    page_dir.mkdir(parents=True, exist_ok=True)
    index = page_dir / "index.html"
    index.write_text(html, encoding="utf-8")
    return index


def copy_static(out_dir: Path) -> None:
    static_out = out_dir / "static"
    if static_out.exists():
        shutil.rmtree(static_out)
    shutil.copytree(HERE / "static", static_out)
    (out_dir / ".nojekyll").write_text("")
