"""Download a map scan, hide its giveaways, and cut it into Deep Zoom (DZI) tiles."""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import sys
import tempfile
import time
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image, ImageDraw

from globepoch.catalog import MapEntry

DZI_NS = "http://schemas.microsoft.com/deepzoom/2008"

# Map scans are legitimately enormous; we trust our own catalog's sources.
Image.MAX_IMAGE_PIXELS = None

USER_AGENT = "GlobepochBuilder/0.1 (daily map-dating game; static site build script)"
TILE_SIZE = 510
OVERLAP = 1
#: Every pyramid is ``<name>.dzi`` plus ``<name>_files/<level>/<col>_<row>.jpg``.
DZI_NAME = "map"
REDACTION_INK = (28, 24, 21)


def download(url: str, cache_dir: Path, retries: int = 5) -> Path:
    """Fetch ``url`` into ``cache_dir`` (once) and return the local path."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / hashlib.sha256(url.encode()).hexdigest()[:24]
    if target.exists() and target.stat().st_size > 0:
        return target
    partial = target.with_suffix(".part")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(request, timeout=600) as resp, open(partial, "wb") as fh:
                shutil.copyfileobj(resp, fh, 1 << 20)
                expected = resp.headers.get("Content-Length")
            _check_complete(partial, expected)
            partial.replace(target)
            return target
        except OSError as exc:
            if attempt == retries - 1:
                raise
            wait = 2 ** (attempt + 1)
            print(f"download failed ({exc}); retrying in {wait}s", file=sys.stderr)
            time.sleep(wait)
    raise AssertionError("unreachable")


def fetch_source(entry: MapEntry, cache_dir: Path) -> Path:
    """Return a local copy of the entry's scan, downloading it on first use."""
    if entry.iiif:
        return fetch_iiif(entry.iiif, entry.max_px, cache_dir)
    return download(entry.image_url, cache_dir)


def fetch_iiif(service: str, max_px: int, cache_dir: Path, region_px: int = 2048) -> Path:
    """Download an IIIF image scaled to ``max_px`` on its longest side, region by region.

    Asking an IIIF server for one huge rendering is slow and the connection tends to be
    dropped part-way; small regions come back in about a second each.
    """
    service = service.rstrip("/")
    target = cache_dir / (hashlib.sha256(f"{service}@{max_px}".encode()).hexdigest()[:24] + ".jpg")
    if target.exists() and target.stat().st_size > 0:
        return target
    cache_dir.mkdir(parents=True, exist_ok=True)
    info = iiif_info(service, cache_dir)
    full_w, full_h = int(info["width"]), int(info["height"])
    scale = min(1.0, max_px / max(full_w, full_h))
    out_w, out_h = round(full_w * scale), round(full_h * scale)
    canvas = Image.new("RGB", (out_w, out_h), "white")

    jobs = []
    for top in range(0, out_h, region_px):
        for left in range(0, out_w, region_px):
            right, bottom = min(out_w, left + region_px), min(out_h, top + region_px)
            # Source rectangle in full-resolution pixels for this output rectangle.
            sx0, sy0 = math.floor(left / scale), math.floor(top / scale)
            sx1 = full_w if right == out_w else math.floor(right / scale)
            sy1 = full_h if bottom == out_h else math.floor(bottom / scale)
            size = f"{right - left},{bottom - top}"
            url = f"{service}/{sx0},{sy0},{sx1 - sx0},{sy1 - sy0}/{size}/0/default.jpg"
            jobs.append(((left, top), url))

    with tempfile.TemporaryDirectory(dir=cache_dir) as tmp, ThreadPoolExecutor(4) as pool:
        paths = pool.map(lambda job: download(job[1], Path(tmp)), jobs)
        for (pos, _url), path in zip(jobs, paths, strict=True):
            with Image.open(path) as part:
                canvas.paste(part.convert("RGB"), pos)
    partial = target.with_suffix(".part.jpg")
    canvas.save(partial, quality=94)
    partial.replace(target)
    return target


def iiif_info(service: str, cache_dir: Path) -> dict:
    """The service's ``info.json`` (size, tiling, profile), cached under ``cache_dir/iiif-info``."""
    service = service.rstrip("/")
    target = cache_dir / "iiif-info" / (hashlib.sha256(service.encode()).hexdigest()[:24] + ".json")
    if target.exists():
        return json.loads(target.read_text())
    request = urllib.request.Request(service + "/info.json", headers={"User-Agent": USER_AGENT})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(request, timeout=120) as resp:
                info = json.load(resp)
            break
        except (OSError, ValueError) as exc:
            if attempt == 4:
                raise
            print(f"info.json failed ({exc}); retrying", file=sys.stderr)
            time.sleep(2 ** (attempt + 1))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(info))
    return info


def _check_complete(path: Path, expected_length: str | None) -> None:
    """IIIF servers sometimes drop the connection mid-image without an error; catch that."""
    size = path.stat().st_size
    if expected_length is not None and size != int(expected_length):
        raise OSError(f"truncated download: got {size} of {expected_length} bytes")
    with open(path, "rb") as fh:
        head = fh.read(3)
        fh.seek(max(0, size - 2))
        tail = fh.read(2)
    if head == b"\xff\xd8\xff" and tail != b"\xff\xd9":
        raise OSError("truncated download: JPEG has no end-of-image marker")


def _px(box, width: int, height: int) -> tuple[int, int, int, int]:
    left, top, right, bottom = box
    return (
        round(left * width),
        round(top * height),
        round(right * width),
        round(bottom * height),
    )


def prepare(source: Path, entry: MapEntry) -> Image.Image:
    """Open the scan, crop it, scale it down to ``entry.max_px`` and paint over redactions."""
    with Image.open(source) as im:
        img = im.convert("RGB")
    if entry.crop:
        img = img.crop(_px(entry.crop, *img.size))
    longest = max(img.size)
    if longest > entry.max_px:
        scale = entry.max_px / longest
        img = img.resize(
            (max(1, round(img.width * scale)), max(1, round(img.height * scale))),
            Image.Resampling.LANCZOS,
        )
    return redact(img, entry.redact)


def redact(img: Image.Image, boxes) -> Image.Image:
    if not boxes:
        return img
    draw = ImageDraw.Draw(img)
    for box in boxes:
        draw.rectangle(_px(box, *img.size), fill=REDACTION_INK)
    return img


def write_dzi(
    img: Image.Image,
    out_dir: Path,
    name: str = DZI_NAME,
    tile_size: int = TILE_SIZE,
    overlap: int = OVERLAP,
    quality: int = 82,
) -> dict:
    """Write ``name.dzi`` and ``name_files/<level>/<col>_<row>.jpg`` into ``out_dir``.

    Returns the pyramid as an OpenSeadragon inline tile source (see :func:`dzi_source`).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    files = out_dir / f"{name}_files"
    if files.exists():
        shutil.rmtree(files)
    width, height = img.size
    max_level = math.ceil(math.log2(max(width, height))) if max(width, height) > 1 else 0

    level_img = img
    for level in range(max_level, -1, -1):
        scale = 2 ** (max_level - level)
        size = (max(1, math.ceil(width / scale)), max(1, math.ceil(height / scale)))
        if level_img.size != size:
            level_img = level_img.resize(size, Image.Resampling.LANCZOS)
        level_dir = files / str(level)
        level_dir.mkdir(parents=True)
        cols = math.ceil(size[0] / tile_size)
        rows = math.ceil(size[1] / tile_size)
        for col in range(cols):
            for row in range(rows):
                x0 = max(0, col * tile_size - overlap)
                y0 = max(0, row * tile_size - overlap)
                x1 = min(size[0], (col + 1) * tile_size + overlap)
                y1 = min(size[1], (row + 1) * tile_size + overlap)
                tile = level_img.crop((x0, y0, x1, y1))
                tile.save(level_dir / f"{col}_{row}.jpg", quality=quality, optimize=True)

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<Image xmlns="{DZI_NS}" Format="jpg" Overlap="{overlap}" TileSize="{tile_size}">'
        f'<Size Width="{width}" Height="{height}"/></Image>\n'
    )
    (out_dir / f"{name}.dzi").write_text(xml)
    return dzi_source(xml, name)


def dzi_source(xml: str, name: str = DZI_NAME) -> dict:
    """Turn a ``.dzi`` file into an OpenSeadragon inline tile source.

    Its ``Url`` is relative to the directory holding the ``.dzi`` file. Inlining the
    descriptor saves the browser a request and lets the tiles live on another origin
    without CORS.
    """
    image = ET.fromstring(xml)
    size = image.find(f"{{{DZI_NS}}}Size")
    if image.tag != f"{{{DZI_NS}}}Image" or size is None:
        raise ValueError("not a Deep Zoom descriptor")
    return {
        "Image": {
            "xmlns": DZI_NS,
            "Url": f"{name}_files/",
            "Format": image.get("Format"),
            "Overlap": image.get("Overlap"),
            "TileSize": image.get("TileSize"),
            "Size": {"Width": size.get("Width"), "Height": size.get("Height")},
        }
    }


def write_preview(img: Image.Image, path: Path, width: int = 1200) -> None:
    """A small JPEG for link previews (og:image)."""
    preview = img.copy()
    preview.thumbnail((width, width), Image.Resampling.LANCZOS)
    preview.save(path, quality=80, optimize=True)
