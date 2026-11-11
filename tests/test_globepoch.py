import datetime as dt
import io
import json
import re
import shutil
import urllib.error
from pathlib import Path

import pytest
from PIL import Image

from globepoch import build, site, tiles
from globepoch.__main__ import main
from globepoch.catalog import Catalog, CatalogError, MapEntry

EPOCH = dt.date(2026, 11, 10)


def entry(i: int, date=None, **extra) -> dict:
    return {
        "id": f"map-{i}",
        "year": 1500 + i,
        "title": f"Map {i}",
        "creator": "Someone",
        "place": "Somewhere",
        "source_page": "https://example.org/item",
        "iiif": "https://example.org/iiif/map",
        "credit": "Example Library",
        "blurb": "A map.",
        **({"date": date} if date else {}),
        **extra,
    }


def catalog(scheduled: int = 2, backlog: int = 2) -> Catalog:
    maps = [entry(i, EPOCH + dt.timedelta(days=i)) for i in range(scheduled)]
    maps += [entry(100 + i) for i in range(backlog)]
    return Catalog.from_dict({"epoch": EPOCH, "map": maps})


# ---------------------------------------------------------------- catalog


def test_real_catalog_is_valid():
    cat = Catalog.load()
    assert len(cat.by_date) >= 12
    assert len(cat.backlog) >= 12
    assert cat.map_for(cat.epoch) is cat.by_date[cat.epoch]


def test_schedule_then_backlog_in_order():
    cat = catalog(scheduled=2, backlog=2)
    days = [EPOCH + dt.timedelta(days=i) for i in range(4)]
    assert [cat.map_for(d).id for d in days] == ["map-0", "map-1", "map-100", "map-101"]
    assert [cat.puzzle_number(d) for d in days] == [1, 2, 3, 4]


def test_running_out_of_maps_is_an_error():
    cat = catalog(scheduled=1, backlog=1)
    with pytest.raises(CatalogError, match="Add more maps"):
        cat.map_for(EPOCH + dt.timedelta(days=2))
    with pytest.raises(CatalogError, match="before the first puzzle"):
        cat.map_for(EPOCH - dt.timedelta(days=1))


def test_schedule_gaps_and_duplicates_are_rejected():
    with pytest.raises(CatalogError, match="gaps"):
        maps = [entry(0, EPOCH), entry(2, EPOCH + dt.timedelta(days=2))]
        Catalog.from_dict({"epoch": EPOCH, "map": maps})
    with pytest.raises(CatalogError, match="duplicate"):
        Catalog.from_dict({"epoch": EPOCH, "map": [entry(0, EPOCH), entry(0)]})
    with pytest.raises(CatalogError, match="same date"):
        Catalog.from_dict({"epoch": EPOCH, "map": [entry(0, EPOCH), entry(1, EPOCH)]})


def test_bad_boxes_and_sources_are_rejected():
    with pytest.raises(CatalogError, match="box"):
        MapEntry.from_dict(entry(0, redact=[[0.5, 0.5, 0.4, 0.6]]))
    with pytest.raises(CatalogError, match="exactly one"):
        Catalog.from_dict({"epoch": EPOCH, "map": [entry(0, image_url="https://x/y.jpg")]})


def test_slug_hides_the_id_and_changes_with_the_pixels():
    e = MapEntry.from_dict(entry(0))
    slug = build.tile_slug(e)
    assert re.fullmatch(r"[0-9a-f]{20}", slug)
    assert "1500" not in slug and "map" not in slug
    redacted = MapEntry.from_dict(entry(0, redact=[[0.1, 0.1, 0.2, 0.2]]))
    assert build.tile_slug(redacted) != slug


# ---------------------------------------------------------------- answer obfuscation


def test_obfuscation_round_trips_and_hides_the_year():
    payload = {"year": 1893, "title": "Café Ü 🗺️"}
    blob = site.obfuscate(payload, 7)
    assert "1893" not in blob
    assert site.deobfuscate(blob, 7) == payload
    with pytest.raises(ValueError):  # wrong key: garbage bytes
        site.deobfuscate(blob, 8)


def test_keystream_matches_the_javascript_lcg():
    # app.js computes x = (Math.imul(x, 1103515245) + 12345) & 0x7fffffff, i.e. arithmetic on
    # the low 32 bits. Python's big ints must give the same bytes.
    def js_like(seed, n):
        x, out = seed, []
        for _ in range(n):
            low = (x * 1103515245) & 0xFFFFFFFF
            if low >= 2**31:
                low -= 2**32
            x = (low + 12345) & 0x7FFFFFFF
            out.append((x >> 16) & 0xFF)
        return bytes(out)

    for number in (1, 2, 365, 10_000):
        seed = number * 7919 + 17
        assert site._keystream(seed, 64) == js_like(seed, 64)


# ---------------------------------------------------------------- image processing


def test_prepare_crops_scales_and_redacts(tmp_path):
    src = tmp_path / "scan.png"
    Image.new("RGB", (400, 200), (255, 255, 255)).save(src)
    e = MapEntry.from_dict(entry(0, crop=[0, 0, 0.5, 1], redact=[[0, 0, 0.5, 0.5]], max_px=100))
    img = tiles.prepare(src, e)
    assert img.size == (100, 100)
    assert img.getpixel((10, 10)) == tiles.REDACTION_INK
    assert img.getpixel((90, 90)) == (255, 255, 255)


def test_write_dzi_pyramid(tmp_path):
    img = Image.new("RGB", (1000, 600), (10, 20, 30))
    source = tiles.write_dzi(img, tmp_path, tile_size=256, overlap=1)
    assert source["Image"]["Size"] == {"Width": "1000", "Height": "600"}
    files = tmp_path / "map_files"
    levels = sorted(int(p.name) for p in files.iterdir())
    assert levels == list(range(11))  # ceil(log2(1000)) == 10
    top = files / "10"
    assert len(list(top.iterdir())) == 4 * 3
    with Image.open(top / "0_0.jpg") as t:
        assert t.size == (257, 257)  # tile plus right/bottom overlap
    with Image.open(top / "3_2.jpg") as t:
        assert t.size == (1000 - 768 + 1, 600 - 512 + 1)
    with Image.open(files / "0" / "0_0.jpg") as t:
        assert t.size == (1, 1)
    assert 'TileSize="256"' in (tmp_path / "map.dzi").read_text()


def test_fetch_iiif_stitches_regions(tmp_path, monkeypatch):
    full = Image.new("RGB", (3000, 1500))
    for x in range(0, 3000, 100):
        full.paste((x % 255, 100, 200), (x, 0, x + 100, 1500))

    class FakeResponse(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(request, timeout=None):
        assert request.full_url.endswith("/info.json")
        return FakeResponse(json.dumps({"width": 3000, "height": 1500}).encode())

    requested = []

    def fake_download(url, cache_dir, retries=5):
        requested.append(url)
        region, size = url.split("/")[-4:-2]
        x, y, w, h = map(int, region.split(","))
        sw, sh = map(int, size.split(","))
        path = Path(cache_dir) / f"{len(requested)}.png"
        full.crop((x, y, x + w, y + h)).resize((sw, sh)).save(path)
        return path

    monkeypatch.setattr(tiles.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(tiles, "download", fake_download)
    out = tiles.fetch_iiif("https://iiif.example/svc/", 1200, tmp_path, region_px=512)
    with Image.open(out) as img:
        assert img.size == (1200, 600)
        r, g, b = img.getpixel((5, 300))
        assert abs(g - 100) < 6 and abs(b - 200) < 6
    assert len(requested) == 3 * 2
    assert all(u.startswith("https://iiif.example/svc/") for u in requested)
    # Cached: a second call does no network work.
    assert tiles.fetch_iiif("https://iiif.example/svc", 1200, tmp_path) == out
    assert len(requested) == 6


def test_check_complete_detects_truncation(tmp_path):
    p = tmp_path / "x.jpg"
    p.write_bytes(b"\xff\xd8\xff" + b"0" * 10)
    with pytest.raises(OSError, match="end-of-image"):
        tiles._check_complete(p, None)
    with pytest.raises(OSError, match="truncated"):
        tiles._check_complete(p, "999")
    p.write_bytes(b"\xff\xd8\xff" + b"0" * 10 + b"\xff\xd9")
    tiles._check_complete(p, str(p.stat().st_size))


# ---------------------------------------------------------------- site


def puzzle_data(index: Path) -> dict:
    html = index.read_text()
    return json.loads(re.search(r'id="puzzle-data"[^>]*>(.*?)</script>', html, re.S).group(1))


@pytest.fixture
def fake_sources(monkeypatch):
    """No network: every scan is a 3000x2000 white image. Records (id, max_px) per download."""
    fetched = []

    def fake_fetch_source(entry, cache_dir):
        fetched.append((entry.id, entry.max_px))
        path = Path(cache_dir) / f"{entry.id}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (3000, 2000), "white").save(path)
        return path

    monkeypatch.setattr(tiles, "fetch_source", fake_fetch_source)
    return fetched


def local_store(tmp_path, site_dir):
    return build.TileStore(cache_dir=tmp_path / "cache", site_dir=site_dir)


def test_build_site_with_archive(tmp_path, fake_sources):
    maps = [entry(i, EPOCH + dt.timedelta(days=i)) for i in range(4)]
    maps[1]["redact"] = [[0.5, 0.5, 1.0, 1.0]]
    cat = Catalog.from_dict({"epoch": EPOCH, "map": maps})
    today = EPOCH + dt.timedelta(days=2)
    out = tmp_path / "site"
    (out / "4").mkdir(parents=True)  # a stale page for a future day must disappear
    build.build_site(out, cat, today, "https://x.test/game", local_store(tmp_path, out))

    # Today's map and earlier ones are tiled; the future map is nowhere.
    assert sorted(fake_sources) == [("map-0", 10000), ("map-1", 10000), ("map-2", 10000)]
    slug = {i: build.tile_slug(cat.map_for(EPOCH + dt.timedelta(days=i))) for i in range(3)}
    assert {p.name for p in (out / "tiles").iterdir()} == set(slug.values())
    assert sorted(p.name for p in out.iterdir() if p.name.isdigit()) == ["1", "2", "3"]
    assert (out / "static" / "app.js").exists()
    assert (out / "tiles" / slug[2] / "preview.jpg").exists()

    root = out / "index.html"
    html = root.read_text()
    assert "Globepoch #3" in html and "Archive puzzle" not in html
    assert "1502" not in html and "Map 2" not in html  # the answer stays hidden
    assert f'content="https://x.test/game/tiles/{slug[2]}/preview.jpg"' in html
    assert 'rel="preconnect" href="https://x.test"' not in html
    data = puzzle_data(root)
    assert (data["number"], data["latest"], data["base"]) == (3, 3, "")
    assert data["shareUrl"] == "https://x.test/game/"
    assert data["viewer"]["source"]["Image"]["Url"] == f"tiles/{slug[2]}/map_files/"
    assert data["viewer"]["source"]["Image"]["Size"] == {"Width": "3000", "Height": "2000"}
    assert [d["n"] for d in data["days"]] == [1, 2, 3]
    assert (data["maxGuesses"], data["yearsPerPoint"]) == (3, 10)
    assert site.deobfuscate(data["answer"], 3)["year"] == 1502

    # Today's puzzle is also reachable at /3/, with paths relative to the subdirectory.
    data3 = puzzle_data(out / "3" / "index.html")
    assert data3["base"] == "../"
    assert data3["viewer"]["source"]["Image"]["Url"] == f"../tiles/{slug[2]}/map_files/"

    # Earlier days have their own tiles with redactions burnt in.
    html2 = (out / "2" / "index.html").read_text()
    assert "Archive puzzle" in html2 and 'href="../static/style.css"' in html2
    data2 = puzzle_data(out / "2" / "index.html")
    assert data2["shareUrl"] == "https://x.test/game/2/"
    assert data2["viewer"]["source"]["Image"]["Url"] == f"../tiles/{slug[1]}/map_files/"
    assert site.deobfuscate(data2["answer"], 2)["year"] == 1501
    top = max(int(p.name) for p in (out / "tiles" / slug[1] / "map_files").iterdir())
    with Image.open(out / "tiles" / slug[1] / "map_files" / str(top) / "5_3.jpg") as tile:
        assert max(tile.convert("L").getextrema()) < 60  # bottom-right quarter is blacked out


def test_build_site_reuses_cached_tiles(tmp_path, fake_sources):
    cat = catalog()
    day2 = EPOCH + dt.timedelta(days=1)
    build.build_site(
        tmp_path / "a", cat, day2, "https://x.test", local_store(tmp_path, tmp_path / "a")
    )
    assert len(fake_sources) == 2
    build.build_site(
        tmp_path / "b", cat, day2, "https://x.test", local_store(tmp_path, tmp_path / "b")
    )
    assert len(fake_sources) == 2  # nothing downloaded or tiled again
    assert sorted(p.name for p in (tmp_path / "b" / "tiles").iterdir()) == sorted(
        p.name for p in (tmp_path / "a" / "tiles").iterdir()
    )
    # The next day only the new map is tiled.
    day3 = day2 + dt.timedelta(1)
    build.build_site(
        tmp_path / "c", cat, day3, "https://x.test", local_store(tmp_path, tmp_path / "c")
    )
    assert fake_sources[2:] == [("map-100", 10000)]


def test_build_site_no_archive(tmp_path, fake_sources):
    cat = catalog()
    day2 = EPOCH + dt.timedelta(1)
    store = local_store(tmp_path, tmp_path / "site")
    build.build_site(tmp_path / "site", cat, day2, "https://x.test", store, archive=False)
    assert sorted(p.name for p in (tmp_path / "site").iterdir() if p.name.isdigit()) == ["2"]
    assert len(list((tmp_path / "site" / "tiles").iterdir())) == 1


@pytest.fixture
def bucket(monkeypatch, tmp_path):
    """A fake public bucket at https://bucket.test/tiles backed by a directory."""
    root = tmp_path / "bucket"
    root.mkdir()
    requested = []

    def fake_urlopen(request, timeout=None):
        url = request.full_url
        requested.append(url)
        path = root / url.removeprefix("https://bucket.test/tiles/")
        if not path.is_file():
            raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)
        return io.BytesIO(path.read_bytes())

    monkeypatch.setattr(build.urllib.request, "urlopen", fake_urlopen)
    return root, requested


def test_build_site_with_remote_tiles(tmp_path, fake_sources, bucket):
    root, requested = bucket
    cat = catalog()
    day2 = EPOCH + dt.timedelta(days=1)
    store = build.TileStore(
        cache_dir=tmp_path / "cache",
        tiles_url="https://bucket.test/tiles/",
        upload_dir=tmp_path / "upload",
    )
    out = tmp_path / "site"
    build.build_site(out, cat, day2, "https://x.test", store)

    # Nothing in the bucket yet: both pyramids are staged for upload, none go in the site.
    slugs = [build.tile_slug(cat.map_for(EPOCH + dt.timedelta(days=i))) for i in range(2)]
    assert sorted(p.name for p in (tmp_path / "upload").iterdir()) == sorted(slugs)
    assert (tmp_path / "upload" / slugs[1] / "map.dzi").exists()
    assert not (out / "tiles").exists()
    assert len(requested) == 2

    html = (out / "index.html").read_text()
    assert '<link rel="preconnect" href="https://bucket.test">' in html
    assert f'content="https://bucket.test/tiles/{slugs[1]}/preview.jpg"' in html
    url = f"https://bucket.test/tiles/{slugs[1]}/map_files/"
    assert puzzle_data(out / "index.html")["viewer"]["source"]["Image"]["Url"] == url
    assert puzzle_data(out / "2" / "index.html")["viewer"]["source"]["Image"]["Url"] == url

    # Once uploaded, the next build only reads the descriptors back.
    shutil.copytree(tmp_path / "upload", root, dirs_exist_ok=True)
    shutil.rmtree(tmp_path / "upload")
    shutil.rmtree(tmp_path / "cache")
    build.build_site(out, cat, day2, "https://x.test", store)
    assert len(fake_sources) == 2
    assert not (tmp_path / "upload").exists()
    data = puzzle_data(out / "index.html")
    assert data["viewer"]["source"]["Image"]["Size"] == {"Width": "3000", "Height": "2000"}

    # Preparing tomorrow stages just tomorrow's map.
    build.prepare_days(cat, day2, 1, store)
    assert [p.name for p in (tmp_path / "upload").iterdir()] == [
        build.tile_slug(cat.map_for(day2 + dt.timedelta(1)))
    ]


def test_dzi_round_trips(tmp_path):
    source = tiles.write_dzi(Image.new("RGB", (700, 300)), tmp_path)
    assert tiles.dzi_source((tmp_path / "map.dzi").read_text()) == source
    with pytest.raises(ValueError):
        tiles.dzi_source("<Image/>")


# ---------------------------------------------------------------- CLI


def test_cli_check_and_schedule(capsys):
    assert main(["check"]) == 0
    assert "OK:" in capsys.readouterr().out
    assert main(["schedule", "--date", "2026-11-10", "--days", "3"]) == 0
    out = capsys.readouterr().out
    assert "#1 " in out and "#3 " in out


def test_cli_build_and_prepare_locally(tmp_path, fake_sources, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(build.urllib.request, "urlopen", None)  # no network
    args = ["--date", "2026-11-11", "--tiles-url", ""]
    assert main(["build", "--out", "site", "--no-archive", *args]) == 0
    assert (tmp_path / "site" / "index.html").exists()
    assert len(list((tmp_path / "site" / "tiles").iterdir())) == 1
    assert main(["prepare", *args]) == 0
    assert len(fake_sources) == 2
    assert not (tmp_path / "_tiles").exists()


def test_cli_reports_catalog_errors(capsys):
    assert main(["build", "--date", "2000-01-01"]) == 1
    assert "before the first puzzle" in capsys.readouterr().err
