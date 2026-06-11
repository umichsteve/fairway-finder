# FairwayFinder — Michigan course-card pipeline

Generates a cartoon-style layout card (map + auto-scorecard) for every named
golf course in Michigan (816 courses) from public-domain NAIP aerial imagery
and OpenStreetMap golf vectors.

## What runs where

| Stage | What | Network |
|---|---|---|
| `prefetch` | 8 regional chunks: course boundaries + all `golf=*` ways statewide | 16 Overpass queries total |
| `join` | Spatial-join features to each course bbox, one JSON per course | none |
| `render` | Per course: NAIP aerial + small context query (water/woods/paths) → card PNG | 1 image + 1 query per course, throttled |

Tiers (from `michigan/courses_mi.csv`): 272 vector (full fairway mapping),
208 partial, 336 raster (aerial + boundary only). Cards degrade gracefully —
scorecard renders only when OSM has a clean 9/18 holes with par.

## Run it

```bash
cd fairway-finder-pipeline
git init -b main && git add -A && git commit -m "Michigan course-card pipeline"
gh repo create fairway-finder --private --source=. --push
gh workflow run "Generate Michigan course cards" -R umichsteve/fairway-finder
gh run watch -R umichsteve/fairway-finder
```

When the run finishes (~1.5–2.5 h; 8 parallel shards, staggered starts):

```bash
gh run download -R umichsteve/fairway-finder -n michigan-cards -D cards
```

## Local / spot runs

```bash
pip install -r requirements.txt
cd michigan
python batch_pipeline.py prefetch        # all 8 chunks (resumable)
python batch_pipeline.py join
python batch_pipeline.py render --names "Belvedere Golf Club"
python batch_pipeline.py render --shard 0 8 --limit 5
```

Everything is idempotent: existing cards are skipped, corrupt cache files are
re-fetched, so re-running a failed shard just fills the gaps.

## National run (all 50 states + DC)

`generate-cards-us.yml` scales the same pipeline to ~15–20k US courses:
inventory job (51 state queries) → dynamic matrix of ~55 render units
(≤320 courses each, 16 parallel, every unit resumable and fail-fast) →
merge job emitting `us-cards` (all PNGs) + `courses_us_tiered.csv`.

```bash
gh workflow run generate-cards-us.yml -R umichsteve/fairway-finder
```

Wall time ~10–14 h. **Cost warning:** this consumes ~120 runner-hours.
On a private repo that's ~7,200 Actions minutes (over the Pro quota —
overage billed). Make the repo public first and minutes are free:

```bash
gh repo edit umichsteve/fairway-finder --visibility public --accept-visibility-change-consequences
```

## Attribution

- Aerial imagery: USDA NAIP via USGS (public domain)
- Course geometry: © OpenStreetMap contributors (ODbL) — attribution required
  wherever cards are displayed
