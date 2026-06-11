"""FairwayFinder national (50-state) card generator.

Stages:
  python3 national_pipeline.py inventory            # per-state course lists (resumable)
  python3 national_pipeline.py plan                 # shard into render units, emit matrix
  python3 national_pipeline.py prefetch --unit K    # OSM cells for unit K (resumable)
  python3 national_pipeline.py join --unit K        # per-course joined JSON + tier
  python3 national_pipeline.py render --unit K      # cards (resumable, fail-fast)
"""
import argparse
import json
import math
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pipeline as P

CACHE = 'cache_us'
JOINED = 'joined_us'
CARDS = 'cards_us'
UNIT_SIZE = 320
CELL = 1.5
GOLF_RE = 'fairway|green|bunker|tee|rough|hole|water_hazard|lateral_water_hazard|cartpath|driving_range'

STATES = ['AL','AK','AZ','AR','CA','CO','CT','DE','FL','GA','HI','ID','IL','IN','IA','KS',
          'KY','LA','ME','MD','MA','MI','MN','MS','MO','MT','NE','NV','NH','NJ','NM','NY',
          'NC','ND','OH','OK','OR','PA','RI','SC','SD','TN','TX','UT','VT','VA','WA','WV',
          'WI','WY','DC']

def valid_json(path):
    if not os.path.exists(path):
        return None
    try:
        d = json.load(open(path))
        return d if isinstance(d, dict) and 'elements' in d else None
    except Exception:
        return None

def slugify(state, name):
    return f"{state.lower()}-" + re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')[:60]

def cmd_inventory(states=None):
    os.makedirs(CACHE, exist_ok=True)
    osm_t = os.environ.get('OSM_QUERY_TIMEOUT', '25')
    missing = []
    for st in (states or STATES):
        f = f'{CACHE}/inv_{st}.json'
        if valid_json(f) is not None:
            continue
        open(f, 'w').write('')
        q = (f'[out:json][timeout:{osm_t}];area["ISO3166-2"="US-{st}"][admin_level=4]->.a;'
             '(way["leisure"="golf_course"](area.a);relation["leisure"="golf_course"](area.a););'
             'out center tags;')
        try:
            d = P.overpass(q, f)
            print(f'{st}: {len(d.get("elements", []))} courses', flush=True)
        except Exception as ex:
            print(f'{st} FAILED: {ex}', flush=True)
            missing.append(st)
        time.sleep(2)
    if missing:
        print(f'missing states: {missing} - rerun inventory', flush=True)
        sys.exit(1)
    rows = []
    for st in (states or STATES):
        d = valid_json(f'{CACHE}/inv_{st}.json') or {'elements': []}
        for e in d['elements']:
            t = e.get('tags', {})
            c = e.get('center', {})
            if not t.get('name') or not c:
                continue
            rows.append({'state': st, 'osm_type': e['type'], 'osm_id': e['id'],
                         'name': t['name'], 'lat': round(c['lat'], 5), 'lon': round(c['lon'], 5),
                         'holes_tag': t.get('holes', ''),
                         'slug': slugify(st, t['name'])})
    seen, uniq = set(), []
    for r in rows:
        if r['slug'] in seen:
            r['slug'] = f"{r['slug']}-{r['osm_id']}"
        seen.add(r['slug'])
        uniq.append(r)
    json.dump(uniq, open('us_manifest.json', 'w'))
    print(f'national manifest: {len(uniq)} named courses', flush=True)

def cmd_plan():
    m = json.load(open('us_manifest.json'))
    units = []
    by_state = {}
    for r in m:
        by_state.setdefault(r['state'], []).append(r)
    for st in sorted(by_state):
        rows = sorted(by_state[st], key=lambda r: (round(r['lat'], 1), r['lon']))
        for i in range(0, len(rows), UNIT_SIZE):
            units.append({'unit': len(units), 'state': st,
                          'slugs': [r['slug'] for r in rows[i:i + UNIT_SIZE]]})
    json.dump(units, open('units.json', 'w'))
    print(f'{len(units)} units across {len(by_state)} states', flush=True)
    print('matrix=' + json.dumps({'unit': [u['unit'] for u in units]}), flush=True)

def unit_courses(k):
    units = json.load(open('units.json'))
    m = {r['slug']: r for r in json.load(open('us_manifest.json'))}
    return [m[s] for s in units[k]['slugs']]

def cells_for(courses):
    return sorted({(math.floor(c['lat'] / CELL), math.floor(c['lon'] / CELL)) for c in courses})

def cmd_prefetch(k):
    os.makedirs(CACHE, exist_ok=True)
    osm_t = os.environ.get('OSM_QUERY_TIMEOUT', '40')
    courses = unit_courses(k)
    missing = 0
    for (la, lo) in cells_for(courses):
        s, w = la * CELL - 0.05, lo * CELL - 0.05
        n, e = (la + 1) * CELL + 0.05, (lo + 1) * CELL + 0.05
        for kind, q in [
            ('b', f'[out:json][timeout:{osm_t}];(way["leisure"="golf_course"]({s},{w},{n},{e});'
                  f'relation["leisure"="golf_course"]({s},{w},{n},{e}););out geom;'),
            ('g', f'[out:json][timeout:{osm_t}];way["golf"~"^({GOLF_RE})$"]({s},{w},{n},{e});out geom;')]:
            f = f'{CACHE}/{kind}_{la}_{lo}.json'
            if valid_json(f) is not None:
                continue
            open(f, 'w').write('')
            try:
                d = P.overpass(q, f)
                print(f'cell {kind} {la},{lo}: {len(d.get("elements", []))}', flush=True)
            except Exception as ex:
                print(f'cell {kind} {la},{lo} FAILED: {ex}', flush=True)
                missing += 1
            time.sleep(2)
    if missing:
        print(f'{missing} cell fetch(es) missing - rerun prefetch', flush=True)
        sys.exit(1)
    print('prefetch complete', flush=True)

def cmd_join(k):
    os.makedirs(JOINED, exist_ok=True)
    courses = unit_courses(k)
    cells = cells_for(courses)
    bidx, feats = {}, []
    for (la, lo) in cells:
        b = valid_json(f'{CACHE}/b_{la}_{lo}.json')
        g = valid_json(f'{CACHE}/g_{la}_{lo}.json')
        for e in (b or {'elements': []})['elements']:
            bidx[(e['type'], e['id'])] = e
        feats += (g or {'elements': []})['elements']
    dedup = {e['id']: e for e in feats}
    feats = list(dedup.values())
    centers = []
    for el in feats:
        g = el.get('geometry') or []
        centers.append((sum(p['lat'] for p in g) / len(g), sum(p['lon'] for p in g) / len(g)) if g else None)

    import batch_pipeline as B
    rows, skipped = [], 0
    for c in courses:
        el = bidx.get((c['osm_type'], c['osm_id']))
        rings = P.rings_of(el) if el else []
        rings = [r for r in rings if r]
        if not rings:
            skipped += 1
            continue
        bbox = B.course_bbox(rings)
        w, s, e, n = bbox
        sel = [feats[i] for i, ct in enumerate(centers) if ct and s <= ct[0] <= n and w <= ct[1] <= e]
        fw = sum(1 for x in sel if x.get('tags', {}).get('golf') == 'fairway')
        tier = 'vector' if fw >= 9 else ('partial' if fw >= 3 else 'raster')
        json.dump({'course': c, 'slug': c['slug'], 'bbox': bbox,
                   'city': (el.get('tags', {}) or {}).get('addr:city', ''),
                   'rings': rings, 'features': sel},
                  open(f'{JOINED}/{c["slug"]}.json', 'w'))
        rows.append({**c, 'fairways': fw, 'tier': tier})
    import csv
    with open(f'unit_{k}_courses.csv', 'w', newline='') as fo:
        wcsv = csv.DictWriter(fo, fieldnames=list(rows[0].keys()) if rows else ['slug'])
        wcsv.writeheader()
        wcsv.writerows(rows)
    print(f'unit {k}: joined {len(rows)}, skipped {skipped} (no geometry)', flush=True)

def cmd_render(k, limit=None):
    os.makedirs(CARDS, exist_ok=True)
    os.makedirs(CACHE, exist_ok=True)
    import numpy as np
    import cv2
    import batch_pipeline as B
    B.CARDS = CARDS
    B.CACHE = CACHE
    B.JOINED = JOINED
    courses = unit_courses(k)
    done = fail = streak = 0
    todo = [c for c in courses if os.path.exists(f'{JOINED}/{c["slug"]}.json')]
    if limit:
        todo = todo[:limit]
    for c in todo:
        out_file = f'{CARDS}/{c["slug"]}_card.png'
        if os.path.exists(out_file):
            done += 1
            continue
        try:
            r = B.render_one(f'{JOINED}/{c["slug"]}.json')
            done += 1
            streak = 0
            print(f'[{done}/{len(todo)}] {c["slug"]}: {r}', flush=True)
        except Exception as ex:
            fail += 1
            streak += 1
            print(f'FAILED {c["slug"]}: {ex}', flush=True)
            if streak >= 6:
                print('6 consecutive failures - aborting unit', flush=True)
                sys.exit(1)
        time.sleep(1)
    print(f'unit {k} done: {done} ok, {fail} failed', flush=True)
    if done == 0:
        sys.exit(1)

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('stage', choices=['inventory', 'plan', 'prefetch', 'join', 'render'])
    ap.add_argument('--unit', type=int)
    ap.add_argument('--states')
    ap.add_argument('--limit', type=int)
    a = ap.parse_args()
    if a.stage == 'inventory':
        cmd_inventory(a.states.split(',') if a.states else None)
    elif a.stage == 'plan':
        cmd_plan()
    elif a.stage == 'prefetch':
        cmd_prefetch(a.unit)
    elif a.stage == 'join':
        cmd_join(a.unit)
    else:
        cmd_render(a.unit, a.limit)
