# Globepoch 🧭

*Date the map.* A daily guessing game in the spirit of Wordle and Redactle: every day you get
one high-resolution, public-domain map scan to pan and zoom around, and three guesses at the
year it was made. It runs at <https://globepoch.com>.

- **Guesses & hints:** you pick a year between 1400 and the current year. After each miss
  you're told whether your guess was too early or too late, and whether it was within 100
  years. A guess within 9 years ends the round early. An unfinished round is saved, so
  reloading the page keeps your earlier guesses.
- **Scoring:** your closest guess scores 100 points, minus one point for every full 10 years
  off: within 9 years scores 100, 85 years off scores 92, and 1,000+ years off scores 0.
- **Redactions:** printed dates, library date stamps and pencilled call numbers are painted out
  with black bars, so the answer can't be read off the map.
- **Stats & streaks** (played, average, current/best streak, score distribution) live in the
  player's `localStorage`. A streak counts consecutive puzzles played on the day they came out
  and scoring at least 1 point.
- **Archive:** every earlier puzzle stays playable at `/<n>/`, where `<n>` is the puzzle number.
  The calendar button in the page header lists them all with your scores, and its badge counts
  the ones you haven't played; after you finish a round, a button offers the next unplayed one.
  Archive plays count toward stats but not streaks, and their share text links to that puzzle.
- **Sharing:** *Share result* copies a summary that doesn't give the year away:

  ```text
  🗺️ Globepoch #2 — 99/100 (3/3)
  ⏪🔵 ⏪🟠 ⏩🟠
  🟥🟥🟧🟧🟨🟨🟩🟩🟩⬜
  ⏩ 14 yrs too late · 🔥 3-day streak
  https://globepoch.com/
  ```

  The first line has the score and the guesses used out of three. The second has one token per
  guess: ⏪ the guess was too early, ⏩ too late, 🟠 within 100 years, 🔵 further off, and 🎯
  within 9 years. The third shows the score as a bar of ten squares, coloured red to green from
  left to right; scores bunch up near the top, so the bar fills slowly at first (50 fills one
  square, 90 fills seven, 99 fills nine, and only 100 fills all ten). The fourth says how far
  off the closest guess was, plus your streak when it's two days or more, and the last line is
  the link to the puzzle.

## How it's built

`globepoch` (also `python -m globepoch`) is a small static site generator. For each day from
the first puzzle (`epoch` in `catalog.toml`) up to today, in UTC, it:

1. picks the map for the day from [`catalog.toml`](src/globepoch/catalog.toml): maps with a
   `date` run on that day, and must cover every day from the first puzzle with no gaps or
   duplicates; after the last dated day, the undated maps (the backlog) run one a day in file
   order;
2. downloads the scan from its [IIIF](https://iiif.io/api/image/2.1/) server in 2048-px regions
   and stitches them together (cached in `.cache/globepoch/`);
3. crops it (if the map has a `crop`), scales it (≤ 10,000 px by default) and paints over
   the map's `redact` boxes;
4. cuts a [Deep Zoom](https://openseadragon.github.io/examples/tilesource-dzi/) tile pyramid
   (25–40 MB for a full-size map) plus a 1200-px preview image for link previews. The
   pyramid's directory is named by a hash of the map's id, source, crop, redactions and size,
   so the URL doesn't give the map away and a published pyramid never changes: editing any of
   those gives the map a new pyramid under a new name;
5. renders `index.html` (today) and `<n>/index.html` (every day so far) from
   [`templates/`](src/globepoch/templates) with the answer lightly obfuscated (not secure,
   just no spoilers in view-source), and copies [`static/`](src/globepoch/static).

The browser side is plain JavaScript plus [OpenSeadragon](https://openseadragon.github.io/)
for the zoomable viewer.

### Where the tiles go

In production the pages are on GitHub Pages and the tiles are in the `globepoch`
DigitalOcean Spaces bucket (region `sfo2`), under `tiles/<hash>/`. `globepoch build
--tiles-url <public URL of tiles/>` points the pages at the bucket. For each map it reads
`<hash>/map.dzi` from that URL; if the bucket doesn't have it, the build tiles the map and
puts the pyramid in `_tiles/<hash>/` for the deploy workflow to upload. The pages don't need
CORS on the bucket: each page carries its map's Deep Zoom descriptor inline, and the browser
loads the tiles as plain images.

Without `--tiles-url` (or `GLOBEPOCH_TILES_URL`), the tiles go in the site itself under
`tiles/`, which is what you want for local development.

Future maps never get pages. After each build, the deploy workflow runs `globepoch prepare`,
which tiles the next day's map and puts it in `_tiles/` as well, so its tiles are in the
bucket a day early, at a URL no page links to yet.

## Development

```sh
python -m venv .venv && source .venv/bin/activate
make install            # pip install -e ".[dev]"
make hooks              # optional: lint + format automatically on each commit
make serve              # build today's site with local tiles; play it at http://localhost:8000
```

Other commands:

```sh
globepoch schedule                                     # which map runs on which day
globepoch build --out _site --tiles-url ""             # today's puzzle and the archive
globepoch build --out _site --tiles-url "" --date 2026-11-15   # the site as of that day
make check                                             # ruff lint + format check, then pytest
```

An empty `--tiles-url` keeps the tiles in the site, even if `GLOBEPOCH_TILES_URL` is set.

The first local build downloads every scan up to today, which takes a while; later builds
reuse `.cache/globepoch/`.

## Deployment

[`.github/workflows/deploy.yml`](.github/workflows/deploy.yml) runs on every push to `main`,
on a daily schedule starting at 00:01 UTC (when the new puzzle starts), and by hand from the
Actions tab, where you can give it a date to build the site as it would look on that day. Each
run:

1. **build** — builds the site with `--tiles-url` pointing at the bucket, then runs
   `globepoch prepare` so tomorrow's run finds tomorrow's map already uploaded. Pyramids the bucket doesn't have yet go into a `tiles` artifact.
2. **upload-tiles** — uploads that artifact to `s3://globepoch/tiles/` as public-read with a
   one-year `Cache-Control`. It's skipped when there's nothing new, and it's the only job that
   sees the bucket keys.
3. **deploy** — publishes the pages to GitHub Pages, but only if the upload worked, so a page
   never links to tiles that aren't there.
4. **tomorrow-tiled** — runs only when `globepoch prepare` failed, and fails the run after
   the deploy, so GitHub sends a failure notice (see [Tiling maps locally](#tiling-maps-locally)).

GitHub can start scheduled runs hours late when it's busy, or skip them, so the workflow is
also scheduled at 00:17, 00:43, 01:29 and 03:13 UTC. Each of those rebuilds the same day, so
whichever runs first publishes the new puzzle and the rest change nothing. Once the new puzzle is
due, the page's countdown says it's coming soon and checks the front page every two minutes,
then links to the new map when it's up. [`ci.yml`](.github/workflows/ci.yml) runs the
linters and tests on pushes and pull requests.

### One-time setup

**DigitalOcean Spaces**

1. In the `globepoch` bucket's settings, keep **File Listing** set to *Restricted*, so nobody
   can list the bucket to find tomorrow's tiles. The workflow uploads each file as
   public-read, so the tiles load without the listing.
2. Create a Spaces access key (**API → Spaces Keys**). If you can, limit it to read/write on
   the `globepoch` bucket.
3. Optional: enable the Spaces CDN on the bucket and set the `TILES_URL` variable (below) to
   the CDN endpoint, e.g. `https://globepoch.sfo2.cdn.digitaloceanspaces.com/tiles`.

**GitHub** (repository **Settings**)

1. **Environments → New environment** named `spaces`. Under **Deployment branches and tags**,
   allow only `main`. Add two environment secrets:

   | Secret                      | Value                           |
   | --------------------------- | ------------------------------- |
   | `SPACES_ACCESS_KEY_ID`      | the Spaces access key's ID      |
   | `SPACES_SECRET_ACCESS_KEY`  | the Spaces access key's secret  |

2. Optional: **Secrets and variables → Actions → Variables → New repository variable**
   `TILES_URL`, the public URL of the bucket's `tiles/` folder. It defaults to
   `https://globepoch.sfo2.digitaloceanspaces.com/tiles`.
3. **Pages → Build and deployment → Source:** *GitHub Actions*. Then set **Custom domain** to
   `globepoch.com` and, once the certificate is issued, tick **Enforce HTTPS**. The build takes
   the site's URL (used in share text and link previews) from the Pages settings, so it picks
   up the custom domain on the next run. No `CNAME` file is needed when deploying from Actions.
4. DNS for `globepoch.com` (at Porkbun, already set):
   - `A` records on the apex: `185.199.108.153`, `185.199.109.153`, `185.199.110.153`,
     `185.199.111.153`;
   - `AAAA` records on the apex: `2606:50c0:8000::153`, `2606:50c0:8001::153`,
     `2606:50c0:8002::153`, `2606:50c0:8003::153`;
   - `CNAME` for `www` pointing at `swenson.github.io`;
   - `TXT` record `_github-pages-challenge-swenson` with the verification code from your
     GitHub account's **Settings → Pages**.

   Porkbun's parking records (an apex `ALIAS` and a `*` wildcard `CNAME`) and its URL forward
   are removed; a wildcard record would let any subdomain point at GitHub Pages.

### Tiling maps locally

The build job downloads scans from the collection's IIIF server, and that server can refuse
GitHub's runners (the Library of Congress has answered them with `429 Too Many Requests`).
When the build can't tile tomorrow's map, it still publishes today's, and then the
`tomorrow-tiled` job fails the run so GitHub sends a failure notice. To tile maps on your own
machine and upload them so the workflow never has to download them:

```sh
globepoch prepare --days 30 --upload-dir _tiles   # pyramids for the next 30 days that the bucket lacks
scripts/upload-tiles.sh                           # upload _tiles/ to the bucket
```

`upload-tiles.sh` needs the AWS CLI and a Spaces key in `~/.config/globepoch/spaces.env`,
with `SPACES_ACCESS_KEY_ID=` and `SPACES_SECRET_ACCESS_KEY=` lines. `prepare` uses
`GLOBEPOCH_TILES_URL` for the bucket's URL, or `--tiles-url`.

The bucket name and endpoint are set at the top of `deploy.yml` (`SPACES_BUCKET`,
`SPACES_ENDPOINT`) if they ever change.

## Adding maps

Each map is a `[[map]]` block in `src/globepoch/catalog.toml`:

```toml
[[map]]
id = "rocque-london"            # used by `globepoch build --map`
date = 2026-11-20               # optional; leave it out to add the map to the backlog
year = 1746                     # the answer
title = "A plan of the cities of London and Westminster, and borough of Southwark"
creator = "John Rocque (engraved by John Pine)"
place = "London, England"
source_page = "https://www.loc.gov/item/76696823/"
iiif = "https://tile.loc.gov/image-services/iiif/service:gmd:gmd5:g5754:g5754l:ct004187"
max_px = 12000                  # optional; longest side of the hosted image (default 10000)
credit = "Library of Congress, Geography and Map Division"
redact = [                      # [left, top, right, bottom], fractions of the (cropped) image
  [0.484, 0.932, 0.515, 0.956],
]
blurb = "Shown with the answer after the round ends."
```

A map without an IIIF service can use `image_url` (a direct link to the scan) instead of
`iiif`, and `crop = [left, top, right, bottom]` keeps only part of a scan. The header of
`catalog.toml` lists every field. `globepoch schedule` warns when the maps are about to run out.

Good sources are collections with IIIF servers and clear public-domain status. Everything so
far comes from the Library of Congress Geography & Map Division, whose search results link to
`tile.loc.gov` IIIF services. Prefer maps whose year is well established (not "ca." or a
range). Add `redact` boxes over anything that prints the date. To get a box's fractions, find
its pixel corners in any image viewer and divide by the image's width and height (the IIIF
`info.json` gives the full size). `--map` builds that map as if it were today's puzzle,
without the archive and always with local tiles, so you can check the boxes:

```sh
globepoch build --map rocque-london --out /tmp/preview
python -m http.server -d /tmp/preview 8001
```

## License

The code is under the [GNU Affero General Public License v3](LICENSE). The map scans come
from the collections credited in `catalog.toml`.
