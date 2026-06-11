"""Extract per-course stats (holes mapped, par, yardage, derived difficulty).

  python3 extract_stats.py prefetch   # holes-only + boundary cells (light, resumable)
  python3 extract_stats.py compute    # -> stats_us.csv

Difficulty (1-5) is a documented proxy: yards-per-par-stroke, banded.
Courses without mapped holes (or partial mapping <9 holes) get null.
Requires us_manifest.json (from national_pipeline.py inventory).
"""
import csv
import json
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pipeline as P
import national_pipeline as N

CACHE = N.CACHE
CELL = N.CELL

def cells_all(manifest):
    return sorted({(math.floor(c['lat'] / CELL), math.floor(c['lon'] / CELL)) for c in manifest})

def cmd_prefetch():
    os.makedirs(CACHE, exist_ok=True)
    manifest = json.load(open('us_manifest.json'))
    osm_t = os.environ.get('OSM_QUERY_TIMEOUT', '40')
    missing = 0
    for (la, lo) in cells_all(manifest):
        s, w = la * CELL - 0.05, lo * CELL - 0.05
        n, e = (la + 1) * CELL + 0.05, (lo + 1) * CELL + 0.05
        for kind, q in [
            ('b', f'[out:json][timeout:{osm_t}];(way["leisure"="golf_course"]({s},{w},{n},{e});'
                  f'relation["leisure"="golf_course"]({s},{w},{n},{e}););out geom;'),
            ('h', f'[out:json][timeout:{osm_t}];way["golf"="hole"]({s},{w},{n},{e});out geom;')]:
            f = f'{CACHE}/{kind}_{la}_{lo}.json'
            if N.valid_json(f) is not None:
                continue
            open(f, 'w').write('')
            try:
                d = P.overpass(q, f)
                print(f'{kind} {la},{lo}: {len(d.get("elements", []))}', flush=True)
            except Exception as ex:
                print(f'{kind} {la},{lo} FAILED: {ex}', flush=True)
                missing += 1
            time.sleep(2)
    if missing:
        print(f'{missing} cells missing - rerun', flush=True)
        sys.exit(1)
    print('prefetch complete', flush=True)

def hole_ways_for_cell(la, lo):
    h = N.valid_json(f'{CACHE}/h_{la}_{lo}.json')
    if h is not None:
        return [e for e in h['elements'] if 'geometry' in e]
    g = N.valid_json(f'{CACHE}/g_{la}_{lo}.json')
    if g is not None:
        return [e for e in g['elements']
                if e.get('tags', {}).get('golf') == 'hole' and 'geometry' in e]
    return []

def difficulty_from(yards, par):
    if not yards or not par or par < 27:
        return None
    ypp = yards / par
    if ypp < 80:
        return 1
    if ypp < 87:
        return 2
    if ypp < 94:
        return 3
    if ypp < 100:
        return 4
    return 5

def cmd_compute():
    manifest = json.load(open('us_manifest.json'))
    bidx = {}
    holes_by_cell = {}
    for (la, lo) in cells_all(manifest):
        b = N.valid_json(f'{CACHE}/b_{la}_{lo}.json')
        for e in (b or {'elements': []})['elements']:
            bidx[(e['type'], e['id'])] = e
        holes_by_cell[(la, lo)] = hole_ways_for_cell(la, lo)

    rows = []
    for c in manifest:
        el = bidx.get((c['osm_type'], c['osm_id']))
        rings = [r for r in (P.rings_of(el) if el else []) if r]
        rec = {'slug': c['slug'], 'name': c['name'], 'state': c['state'],
               'holes_mapped': 0, 'par': '', 'yards': '', 'difficulty': ''}
        if rings:
            import batch_pipeline as B
            w, s, e, n = B.course_bbox(rings)
            cands = []
            for dla in (-1, 0, 1):
                for dlo in (-1, 0, 1):
                    cands += holes_by_cell.get(
                        (math.floor(c['lat'] / CELL) + dla, math.floor(c['lon'] / CELL) + dlo), [])
            mine = []
            seen = set()
            for hw in cands:
                if hw['id'] in seen:
                    continue
                seen.add(hw['id'])
                g = hw['geometry']
                clat = sum(p['lat'] for p in g) / len(g)
                clon = sum(p['lon'] for p in g) / len(g)
                if s <= clat <= n and w <= clon <= e:
                    mine.append(hw)
            pars = [int(h['tags']['par']) for h in mine
                    if str(h.get('tags', {}).get('par', '')).isdigit()]
            yards = sum(P.hole_yards(h['geometry']) for h in mine)
            rec['holes_mapped'] = len(mine)
            if len(mine) >= 9 and len(pars) >= 9:
                par = sum(pars)
                rec['par'] = par
                rec['yards'] = yards
                d = difficulty_from(yards, par)
                rec['difficulty'] = d if d is not None else ''
        rows.append(rec)
    with open('stats_us.csv', 'w', newline='') as fo:
        wcsv = csv.DictWriter(fo, fieldnames=list(rows[0].keys()))
        wcsv.writeheader()
        wcsv.writerows(rows)
    withstats = sum(1 for r in rows if r['par'])
    print(f'{len(rows)} courses, {withstats} with par/yardage/difficulty', flush=True)

if __name__ == '__main__':
    if sys.argv[1] == 'prefetch':
        cmd_prefetch()
    else:
        cmd_compute()
