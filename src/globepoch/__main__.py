"""Command line for building Globepoch: ``python -m globepoch <command>``.

Commands:
  build      render the site for a day (default: today, UTC) plus the archive of earlier days
  prepare    tile the next few days' maps ahead of time
  schedule   show which map runs on which upcoming day
  check      validate catalog.toml
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from globepoch.catalog import CATALOG_PATH, Catalog, CatalogError

DEFAULT_SITE_URL = os.environ.get("GLOBEPOCH_SITE_URL", "http://localhost:8000/")
DEFAULT_TILES_URL = os.environ.get("GLOBEPOCH_TILES_URL") or None


def _today_utc() -> dt.date:
    return dt.datetime.now(dt.timezone.utc).date()


def _store(args: argparse.Namespace, site_dir: Path | None = None):
    from globepoch.build import TileStore

    return TileStore(
        cache_dir=Path(args.cache),
        site_dir=site_dir,
        tiles_url=args.tiles_url,
        upload_dir=Path(args.upload_dir),
    )


def _cmd_build(args: argparse.Namespace) -> int:
    # Imported lazily so `schedule`/`check` work without Pillow and Jinja2.
    from globepoch import build

    catalog = Catalog.load(args.catalog)
    day = args.date or _today_utc()
    if args.map:
        # Preview one map as if it were today's puzzle (no archive, so nothing else leaks).
        entry = catalog.get(args.map)
        catalog.by_date = {**catalog.by_date, day: entry}
        args.tiles_url = None  # previews keep their tiles to themselves
    out = Path(args.out)
    build.build_site(
        out,
        catalog,
        day,
        args.site_url,
        _store(args, out),
        archive=not (args.map or args.no_archive),
    )
    print(f"  wrote {out}/")
    return 0


def _cmd_prepare(args: argparse.Namespace) -> int:
    from globepoch import build

    catalog = Catalog.load(args.catalog)
    build.prepare_days(catalog, args.date or _today_utc(), args.days, _store(args))
    return 0


def _cmd_schedule(args: argparse.Namespace) -> int:
    catalog = Catalog.load(args.catalog)
    start = args.date or _today_utc()
    for day, number, entry in catalog.upcoming(start, args.days):
        if entry is None:
            print(f"#{number:<4} {day}  -- NO MAP (add to the backlog!) --")
            continue
        source = "scheduled" if entry.date else "backlog"
        print(f"#{number:<4} {day}  {source:<9}  {entry.year}  {entry.id}")
    left = (catalog.last_scheduled - start).days + 1 + len(catalog.backlog)
    print(f"\n{len(catalog.by_date)} scheduled, {len(catalog.backlog)} in backlog; ", end="")
    print(f"maps run out after {start + dt.timedelta(days=max(left, 0) - 1)}.")
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    catalog = Catalog.load(args.catalog)
    years = sorted(m.year for m in catalog.maps)
    print(f"OK: {len(catalog.maps)} maps, years {years[0]}–{years[-1]}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="globepoch", description=__doc__.splitlines()[0])
    parser.add_argument("--catalog", type=Path, default=CATALOG_PATH)
    sub = parser.add_subparsers(dest="command", required=True)

    def tile_options(p: argparse.ArgumentParser) -> None:
        p.add_argument("--cache", default=".cache/globepoch", help="scans and tiles cache")
        p.add_argument(
            "--tiles-url",
            default=DEFAULT_TILES_URL,
            help="public URL of the tile pyramids in object storage (default: "
            "$GLOBEPOCH_TILES_URL; unset means the tiles go in the site itself)",
        )
        p.add_argument(
            "--upload-dir",
            default="_tiles",
            help="with --tiles-url, where pyramids missing from storage are put for upload",
        )

    p = sub.add_parser("build", help="build the site for one day")
    p.add_argument("--date", type=dt.date.fromisoformat, help="YYYY-MM-DD (default: today UTC)")
    p.add_argument("--map", help="build this map id instead of the scheduled one (previews)")
    p.add_argument("--out", default="_site")
    p.add_argument("--site-url", default=DEFAULT_SITE_URL, help="public URL used in share text")
    p.add_argument("--no-archive", action="store_true", help="skip pages for earlier days")
    tile_options(p)
    p.set_defaults(func=_cmd_build)

    p = sub.add_parser("prepare", help="tile the maps for the days after --date ahead of time")
    p.add_argument("--date", type=dt.date.fromisoformat, help="today (default: today UTC)")
    p.add_argument("--days", type=int, default=1, help="how many days ahead (default 1)")
    tile_options(p)
    p.set_defaults(func=_cmd_prepare)

    p = sub.add_parser("schedule", help="list upcoming days")
    p.add_argument("--date", type=dt.date.fromisoformat, help="first day (default: today UTC)")
    p.add_argument("--days", type=int, default=30)
    p.set_defaults(func=_cmd_schedule)

    p = sub.add_parser("check", help="validate the catalog")
    p.set_defaults(func=_cmd_check)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except CatalogError as exc:
        print(f"globepoch: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
