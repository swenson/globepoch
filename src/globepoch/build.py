"""Build the site: today's puzzle plus a playable archive of every earlier day.

Layout of the output directory::

    index.html            today's puzzle
    <n>/index.html        puzzle #n, for every day from the epoch up to today
    static/               CSS, JS, icon
    tiles/<slug>/         Deep Zoom tiles, only for local builds (no ``tiles_url``)

Each map is cut into a Deep Zoom tile pyramid named by a hash of everything that changes its
pixels (the "slug"), so a pyramid never changes once it exists. Pyramids are cached locally in
``<cache>/tiles/<slug>``.

In production the tiles live in object storage (a DigitalOcean Spaces bucket) at
``tiles_url``. The build looks for each pyramid's ``map.dzi`` there; a pyramid that isn't
there yet is tiled and copied into ``upload_dir/<slug>/`` for the deploy workflow to upload
before it publishes the pages. Without ``tiles_url``, pyramids are copied into the site
itself so it works on its own (``python -m http.server``).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import shutil
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from globepoch import site, tiles
from globepoch.catalog import Catalog, MapEntry

#: Bump to give every pyramid a new slug (e.g. after changing the tiler).
TILE_VERSION = 1


def _log(msg: str) -> None:
    print(msg, flush=True)


def tile_slug(entry: MapEntry) -> str:
    """An opaque name for the entry's tile pyramid: a hash of everything in its pixels.

    The id usually contains the year, so it can't appear in a URL as is.
    """
    recipe = {
        "v": TILE_VERSION,
        "id": entry.id,
        "source": entry.iiif or entry.image_url,
        "crop": entry.crop,
        "redact": entry.redact,
        "max_px": entry.max_px,
        "tile": [tiles.TILE_SIZE, tiles.OVERLAP],
    }
    return hashlib.sha256(json.dumps(recipe, sort_keys=True).encode()).hexdigest()[:20]


def _link_or_copy(src: str, dst: str) -> None:
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def _copy_tree(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, copy_function=_link_or_copy)


@dataclass
class TileStore:
    """Where tile pyramids are published, and how pages link to them.

    ``tiles_url`` is the public base URL of the pyramids in object storage; ``None`` means
    they're published inside the site at ``<site_dir>/tiles``.
    """

    cache_dir: Path
    site_dir: Path | None = None
    tiles_url: str | None = None
    upload_dir: Path | None = None

    def __post_init__(self) -> None:
        if self.tiles_url:
            self.tiles_url = self.tiles_url.rstrip("/")
            if self.upload_dir is None:
                raise ValueError("a tiles_url needs an upload_dir for new pyramids")

    @property
    def remote(self) -> bool:
        return bool(self.tiles_url)

    def tile(self, entry: MapEntry) -> tuple[Path, dict]:
        """Return the entry's pyramid from the local cache, tiling it first if needed."""
        slug = tile_slug(entry)
        cached = self.cache_dir / "tiles" / slug
        dzi = cached / f"{tiles.DZI_NAME}.dzi"
        if dzi.exists():
            return cached, tiles.dzi_source(dzi.read_text())
        img = tiles.prepare(tiles.fetch_source(entry, self.cache_dir), entry)
        partial = cached.with_name(slug + ".part")
        if partial.exists():
            shutil.rmtree(partial)
        source = tiles.write_dzi(img, partial)
        tiles.write_preview(img, partial / "preview.jpg")
        if cached.exists():
            shutil.rmtree(cached)
        partial.rename(cached)
        _log(f"  {entry.id}: tiled {img.width}x{img.height}")
        return cached, source

    def fetch_remote(self, slug: str) -> dict | None:
        """The published pyramid's DZI source, or ``None`` if it isn't in the bucket yet."""
        url = f"{self.tiles_url}/{slug}/{tiles.DZI_NAME}.dzi"
        request = urllib.request.Request(url, headers={"User-Agent": tiles.USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=60) as resp:
                return tiles.dzi_source(resp.read().decode())
        except urllib.error.HTTPError as exc:
            # Spaces answers 403 rather than 404 for missing keys when file listing is off.
            if exc.code in (403, 404):
                return None
            raise

    def publish(self, entry: MapEntry) -> tuple[dict, str]:
        """Make sure the entry's pyramid is (or will be) published.

        Returns the viewer for :func:`site.render_page` and the URL of the preview image.
        A local store's URLs are relative to the site root.
        """
        slug = tile_slug(entry)
        if self.remote:
            source = self.fetch_remote(slug)
            if source is None:
                cached, source = self.tile(entry)
                _copy_tree(cached, self.upload_dir / slug)
                _log(f"  {entry.id}: staged for upload")
            else:
                _log(f"  {entry.id}: already published")
            base = f"{self.tiles_url}/{slug}/"
        else:
            cached, source = self.tile(entry)
            _copy_tree(cached, self.site_dir / "tiles" / slug)
            base = f"tiles/{slug}/"
        source["Image"]["Url"] = base + source["Image"]["Url"]
        return {"kind": "dzi", "source": source}, base + "preview.jpg"

    def prepare(self, entry: MapEntry) -> None:
        """Get a future map's pyramid ready without putting it in the site.

        A remote store stages it for upload (so the build on its day doesn't have to tile it);
        a local store only warms the cache.
        """
        if self.remote:
            if self.fetch_remote(tile_slug(entry)) is None:
                cached, _ = self.tile(entry)
                _copy_tree(cached, self.upload_dir / tile_slug(entry))
                _log(f"  {entry.id}: staged for upload")
            else:
                _log(f"  {entry.id}: already published")
        else:
            self.tile(entry)


def build_site(
    out_dir: Path,
    catalog: Catalog,
    today: dt.date,
    site_url: str,
    store: TileStore,
    *,
    archive: bool = True,
) -> None:
    """Build the site for ``today`` into ``out_dir``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    # Start from scratch so maps that are no longer published don't linger.
    if (out_dir / "tiles").exists():
        shutil.rmtree(out_dir / "tiles")
    site_url = site_url.rstrip("/") + "/"

    entry = catalog.map_for(today)
    number = catalog.puzzle_number(today)
    _log(f"Globepoch #{number} ({today}): {entry.id}")
    viewer, preview = store.publish(entry)
    if not store.remote:
        preview = site_url + preview
    for page_dir, base in ((out_dir, ""), (out_dir / str(number), "../")):
        site.render_page(
            page_dir, catalog, entry, today, today, viewer, site_url, base=base, preview_url=preview
        )

    if archive and number > 1:
        _log(f"Archive: {number - 1} earlier puzzles")
        for i in range(number - 1):
            day = catalog.epoch + dt.timedelta(days=i)
            past = catalog.map_for(day)
            past_viewer, _ = store.publish(past)
            site.render_page(
                out_dir / str(catalog.puzzle_number(day)),
                catalog,
                past,
                day,
                today,
                past_viewer,
                site_url,
                base="../",
            )

    # Pages for days that are no longer (or not yet) valid would spoil or confuse.
    for child in out_dir.iterdir():
        if child.is_dir() and child.name.isdigit() and not 1 <= int(child.name) <= number:
            shutil.rmtree(child)
    site.copy_static(out_dir)


def prepare_days(catalog: Catalog, start: dt.date, days: int, store: TileStore) -> None:
    """Get the pyramids for the ``days`` days after ``start`` ready ahead of time."""
    for day, number, entry in catalog.upcoming(start + dt.timedelta(days=1), days):
        if entry is None:
            _log(f"#{number} {day}: no map scheduled")
            continue
        _log(f"#{number} {day}: {entry.id}")
        store.prepare(entry)
