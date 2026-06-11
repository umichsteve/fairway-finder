"""FairwayFinder Michigan batch generator.

Stages (run separately, all resumable / idempotent):
  python3 batch_pipeline.py prefetch [chunk_idx]   # statewide golf features + boundaries -> cache/
  python3 batch_pipeline.py join                   # spatial-join features to courses -> joined/
  python3 batch_pipeline.py render --shard K N     # render shard K of N -> cards/
  python3 batch_pipeline.py render --names "A,B"   # render specific courses

Per-course network at render time: 1 NAIP image + 1 small context query
(natural water/wood + paths in bbox). All golf=* features come from the
statewide prefetch cache - no per-course golf queries.
"""
import argparse
import json
import math
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pipeline as P

CACHE = 'cache'
JOINED = 'joined'
CARDS = 'cards'

# Michigan coverage chunks (S,W,N,E) - lower peninsula 2x3 + UP 2
CHUNKS = [
    (41.69, -87.20, 43.40, -84.60), (41.69, -84.60, 43.40, -82.10),
    (43.40, -87.40, 44.85, -84.60), (43.40, -84.60, 44.85, -82.30),
    (44.85, -86.60, 45.95, -83.30), (45.10, -90.50, 46.60, -86.60),
    (45.95, -87.00, 47.60, -83.30), (46.30, -90.50, 48.40, -87.00),
]
GOLF_RE = 'fairway|green|bunker|tee|rough|hole|water_hazard|lateral_water_hazard|cartpath|driving_range'

def overpass_q(query, out_file):
    return P.overpass(query, out_file)

def valid_json(path):
    if not os.path.exists(path):
        return None
    try:
        d = json.load(open(path))
        return d if isinstance(d, dict) and 'elements' in d else None
    except Exception:
        return None

def cmd_prefetch(only_idx=None):
    os.makedirs(CACHE, exist_ok=True)
    osm_t = os.environ.get('OSM_QUERY_TIMEOUT', '22')
    missing = []
    for i, (s, w, n, e) in enumerate(CHUNKS):
        if only_idx is not None and i != only_idx:
            continue
        bf = f'{CACHE}/boundaries_chunk_{i}.json'
        if valid_json(bf) is None:
            open(bf, 'w').write('')
            q = (f'[out:json][timeout:{osm_t}];(way["leisure"="golf_course"]({s},{w},{n},{e});'
                 f'relation["leisure"="golf_course"]({s},{w},{n},{e}););out geom;')
            try:
                d = overpass_q(q, bf)
                print(f'boundaries chunk {i}: {len(d.get("elements", []))}', flush=True)
            except Exception as ex:
                print(f'boundaries chunk {i} FAILED: {ex}', flush=True)
                missing.append(bf)
            time.sleep(3)
        cf = f'{CACHE}/golf_chunk_{i}.json'
        d = valid_json(cf)
        if d is not None:
            print(f'golf chunk {i} cached ({len(d["elements"])} ways)', flush=True)
            continue
        open(cf, 'w').write('')
        q = f'[out:json][timeout:{osm_t}];way["golf"~"^({GOLF_RE})$"]({s},{w},{n},{e});out geom;'
        try:
            d = overpass_q(q, cf)
            print(f'golf chunk {i}: {len(d.get("elements", []))} ways', flush=True)
        except Exception as ex:
            print(f'golf chunk {i} FAILED: {ex}', flush=True)
            missing.append(cf)
        time.sleep(3)
    if missing:
        print(f'{len(missing)} chunk(s) still missing - rerun prefetch', flush=True)
        sys.exit(1)
    print('prefetch complete', flush=True)

def course_bbox(rings, pad=0.06):
    lats = [p['lat'] for r in rings for p in r]
    lons = [p['lon'] for r in rings for p in r]
    s_lat, n_lat, w_lon, e_lon = min(lats), max(lats), min(lons), max(lons)
    dlat, dlon = n_lat - s_lat, e_lon - w_lon
    s_lat -= dlat * pad; n_lat += dlat * pad
    w_lon -= dlon * pad; e_lon += dlon * pad
    lat_m = (n_lat - s_lat) * 110574
    lon_m = (e_lon - w_lon) * 111320 * math.cos(math.radians((n_lat + s_lat) / 2))
    if lat_m > lon_m:
        extra = (lat_m - lon_m) / (111320 * math.cos(math.radians((n_lat + s_lat) / 2))) / 2
        w_lon -= extra; e_lon += extra
    else:
        extra = (lon_m - lat_m) / 110574 / 2
        s_lat -= extra; n_lat += extra
    return (w_lon, s_lat, e_lon, n_lat)

def cmd_join():
    os.makedirs(JOINED, exist_ok=True)
    manifest = json.load(open('mi_manifest.json'))
    bidx = {}
    for i in range(len(CHUNKS)):
        f = f'{CACHE}/boundaries_chunk_{i}.json'
        if os.path.exists(f):
            for e in json.load(open(f))['elements']:
                bidx[(e['type'], e['id'])] = e
    feats = []
    for i in range(len(CHUNKS)):
        f = f'{CACHE}/golf_chunk_{i}.json'
        if os.path.exists(f):
            feats += json.load(open(f))['elements']
    dedup = {}
    for el in feats:
        dedup[el['id']] = el
    feats = list(dedup.values())
    print(f'{len(feats)} unique golf ways statewide', flush=True)

    centers = []
    for el in feats:
        g = el.get('geometry') or []
        if not g:
            centers.append(None)
            continue
        centers.append((sum(p['lat'] for p in g) / len(g), sum(p['lon'] for p in g) / len(g)))

    joined, missing = 0, 0
    for m in manifest:
        el = bidx.get((m['osm_type'], m['osm_id']))
        if not el:
            missing += 1
            continue
        rings = P.rings_of(el)
        if not any(rings):
            missing += 1
            continue
        bbox = course_bbox(rings)
        w, s, e, n = bbox
        sel = [feats[i] for i, c in enumerate(centers)
               if c and s <= c[0] <= n and w <= c[1] <= e]
        slug = re.sub(r'[^a-z0-9]+', '-', m['name'].lower()).strip('-')
        json.dump({'course': m, 'slug': slug, 'bbox': bbox,
                   'city': el.get('tags', {}).get('addr:city', ''),
                   'rings': rings, 'features': sel},
                  open(f'{JOINED}/{slug}.json', 'w'))
        joined += 1
    print(f'joined {joined} courses ({missing} missing geometry)', flush=True)

def render_one(jpath):
    d = json.load(open(jpath))
    slug, bbox, rings = d['slug'], tuple(d['bbox']), d['rings']
    out_file = f'{CARDS}/{slug}_card.png'
    if os.path.exists(out_file):
        return 'cached'
    w, s, e, n = bbox
    naip = f'{CACHE}/{slug}_naip.jpg'
    P.fetch(P.NAIP.format(w=w, s=s, e=e, n=n), naip)
    ctx_file = f'{CACHE}/{slug}_ctx.json'
    bb = f'({s},{w},{n},{e})'
    qctx = ('[out:json][timeout:20];('
            f'way["natural"~"^(water|wood|scrub)$"]{bb};'
            f'way["landuse"="forest"]{bb};'
            f'way["highway"~"^(path|track|service|footway)$"]{bb};);out geom;')
    try:
        ctx = overpass_q(qctx, ctx_file).get('elements', [])
    except Exception:
        ctx = []
    feats = d['features'] + ctx
    import numpy as np
    import cv2
    if not os.path.exists(naip) or os.path.getsize(naip) < 20000:
        blank = np.full((1280, 1280, 3), 200, dtype=np.uint8)
        cv2.imwrite(naip, blank)
    course_map, holes = P.render(f'{CACHE}/{slug}', d['course']['name'], d['city'], bbox, rings, feats)
    strip = P.scorecard_strip(d['course']['name'], d['city'], holes)
    card = np.vstack([course_map, strip])
    if not cv2.imwrite(out_file, card) or not os.path.exists(out_file):
        raise RuntimeError(f'card write failed: {out_file}')
    return f'ok features={len(feats)} holes={len(holes)}'

def cmd_render(shard=None, total=None, names=None, limit=None):
    os.makedirs(CARDS, exist_ok=True)
    os.makedirs(CACHE, exist_ok=True)
    files = sorted(os.listdir(JOINED))
    if names:
        wanted = [re.sub(r'[^a-z0-9]+', '-', x.strip().lower()).strip('-') for x in names.split(',')]
        files = [f for f in files if f[:-5] in wanted]
    elif shard is not None:
        files = [f for i, f in enumerate(files) if i % total == shard]
    if limit:
        files = files[:limit]
    done = fail = streak = 0
    for f in files:
        try:
            r = render_one(f'{JOINED}/{f}')
            done += 1
            streak = 0
            print(f'[{done}/{len(files)}] {f[:-5]}: {r}', flush=True)
        except Exception as ex:
            fail += 1
            streak += 1
            print(f'FAILED {f[:-5]}: {ex}', flush=True)
            if streak >= 6:
                print('6 consecutive failures - aborting shard (systemic problem)', flush=True)
                sys.exit(1)
        time.sleep(1)
    print(f'shard done: {done} ok, {fail} failed', flush=True)
    if done == 0:
        sys.exit(1)

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('stage', choices=['prefetch', 'join', 'render'])
    ap.add_argument('chunk', nargs='?', type=int)
    ap.add_argument('--shard', nargs=2, type=int, metavar=('K', 'N'))
    ap.add_argument('--names')
    ap.add_argument('--limit', type=int)
    a = ap.parse_args()
    if a.stage == 'prefetch':
        cmd_prefetch(a.chunk)
    elif a.stage == 'join':
        cmd_join()
    else:
        cmd_render(shard=a.shard[0] if a.shard else None,
                   total=a.shard[1] if a.shard else None,
                   names=a.names, limit=a.limit)
