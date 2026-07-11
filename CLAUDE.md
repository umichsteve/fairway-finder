# fairway-finder

Pipeline that generates cartoon-style layout cards (map plus auto-scorecard) for golf courses from public-domain NAIP aerial imagery and OpenStreetMap golf vectors.

## Stack

- Python 3 (opencv-python-headless, numpy, pillow); pipeline code in `michigan/` (covers Michigan and national runs)
- Deploy: GitHub Actions (four workflows: generate-cards, generate-cards-us, crawl-cost, extract-stats; cards download as run artifacts)
- Local spot runs: `pip install -r requirements.txt`, then `cd michigan && python batch_pipeline.py prefetch|join|render` (see README for flags)

## Conventions

- Cloud-only workflow: no local dev servers or venvs; verify via preview deploys (where applicable).
- Everything is idempotent and resumable — existing cards are skipped and corrupt cache files re-fetched; preserve that when editing stages.
- Overpass and imagery requests are throttled and sharded (8 parallel shards, staggered starts); don't remove throttling.
- Course tiers come from `michigan/courses_mi.csv` (vector / partial / raster) and cards degrade gracefully when OSM data is incomplete.

## Non-goals

- Don't add frameworks, build steps, or dependencies not already present without asking.

## Handoff protocol

If `HANDOFF.md` exists at the repo root: read it before anything else, treat its "Done when" as acceptance criteria and "Non-goals" as hard boundaries, run its Verify steps, and delete the file in the final commit. Format and full rules: `.claude/skills/pr-handoff/SKILL.md`.
