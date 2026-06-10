"""FairwayFinder course-card pipeline: OSM course -> cartoon layout card.
Usage: python3 pipeline.py  (processes PILOT list; full run via CI)"""
import cv2
import json
import math
import os
import re
import subprocess
import time
import numpy as np
from PIL import Image, ImageDraw, ImageFont

UA = 'FairwayFinder-POC/0.1 (steve.demaagd@gmail.com)'
OVERPASS = ['https://overpass-api.de/api/interpreter',
            'https://overpass.private.coffee/api/interpreter',
            'https://overpass.kumi.systems/api/interpreter']
NAIP = ('https://imagery.nationalmap.gov/arcgis/rest/services/USGSNAIPImagery/'
        'ImageServer/exportImage?bbox={w},{s},{e},{n}&bboxSR=4326&imageSR=4326'
        '&size=1280,1280&format=jpgpng&f=image')
SIZE = 1280
SS = 2
D = SIZE * SS
FB = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'
FR = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'

def b(rgb):
    return (rgb[2], rgb[1], rgb[0])

BG_OUT     = b((238, 244, 236))
BG         = b((110, 168, 112))
ROUGH      = b((127, 184, 128))
WOOD       = b((52, 105, 66))
WOOD_DEEP  = b((34, 79, 52))
FAIRWAY    = b((163, 209, 156))
GREEN      = b((205, 232, 196))
GREEN_LINE = b((62, 133, 83))
BUNKER     = b((232, 236, 226))
BUNK_LINE  = b((178, 192, 172))
WATER      = b((74, 144, 217))
WATER_LINE = b((47, 111, 180))
PATH       = b((226, 233, 224))
PINE       = b((14, 59, 44))
WHITE      = (255, 255, 255)

def overpass(query, out_file):
    if os.path.exists(out_file):
        try:
            with open(out_file) as f:
                return json.load(f)
        except Exception:
            pass
    for url in OVERPASS:
        subprocess.run(['curl', '-s', '-m', '25', '-A', UA, url,
                        '--data-urlencode', f'data={query}', '-o', out_file])
        try:
            with open(out_file) as f:
                return json.load(f)
        except Exception:
            time.sleep(5)
    raise RuntimeError(f'overpass failed: {out_file}')

def fetch(url, out_file):
    if os.path.exists(out_file) and os.path.getsize(out_file) > 20000:
        return
    subprocess.run(['curl', '-s', '-m', '30', '-A', UA, url, '-o', out_file])

def rings_of(el):
    if el['type'] == 'way':
        return [el['geometry']]
    rings = []
    for m in el.get('members', []):
        if m.get('role') in ('outer', '') and 'geometry' in m:
            rings.append(m['geometry'])
    return rings

def hole_yards(geom):
    tot = 0.0
    for i in range(len(geom) - 1):
        a, p = geom[i], geom[i + 1]
        dlat = math.radians(p['lat'] - a['lat'])
        dlon = math.radians(p['lon'] - a['lon'])
        la1, la2 = math.radians(a['lat']), math.radians(p['lat'])
        x = math.sin(dlat / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(dlon / 2) ** 2
        tot += 2 * 6371000 * math.asin(math.sqrt(x))
    return int(round(tot * 1.09361))

def resample(pts, step=5):
    out = [pts[0].astype(np.float64)]
    for i in range(len(pts) - 1):
        a, p = pts[i].astype(np.float64), pts[i + 1].astype(np.float64)
        d = float(np.hypot(*(p - a)))
        n = max(int(d // step), 1)
        for j in range(1, n + 1):
            out.append(a + (p - a) * (j / n))
    return np.array(out)

def draw_dashed(img, pts, color, thickness, dash=26, gap=16):
    rs = resample(pts)
    acc, pen = 0.0, True
    for i in range(len(rs) - 1):
        a, p = rs[i], rs[i + 1]
        if pen:
            cv2.line(img, tuple(a.astype(int)), tuple(p.astype(int)), color, thickness, cv2.LINE_AA)
        acc += float(np.hypot(*(p - a)))
        if pen and acc >= dash:
            pen, acc = False, 0.0
        elif not pen and acc >= gap:
            pen, acc = True, 0.0

def draw_arrow(img, pts, color, size=24):
    rs = resample(pts)
    tip = rs[-1]
    d = rs[-1] - (rs[-6] if len(rs) > 6 else rs[0])
    n = d / (np.hypot(*d) + 1e-9)
    perp = np.array([-n[1], n[0]])
    tri = np.array([tip, tip - n * size + perp * size * 0.55,
                    tip - n * size - perp * size * 0.55], dtype=np.int32)
    cv2.fillPoly(img, [tri], color, lineType=cv2.LINE_AA)

def cartoon_tree_mask(naip_path, bbox, course_rings):
    img = cv2.imread(naip_path)
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    tree = ((h >= 15) & (h <= 100) & (v < 105) & (s > 15)).astype(np.uint8) * 255
    tree = cv2.morphologyEx(tree, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))
    tree = cv2.morphologyEx(tree, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)))
    tree = cv2.GaussianBlur(tree, (15, 15), 0)
    return cv2.resize(tree, (D, D), interpolation=cv2.INTER_LINEAR)

def kind_of(tags):
    g = tags.get('golf')
    if g == 'hole':
        return 'hole'
    if g == 'cartpath' or tags.get('highway') in ('path', 'track', 'service', 'footway'):
        return 'path'
    if g in ('water_hazard', 'lateral_water_hazard') or tags.get('natural') == 'water':
        return 'water'
    if g in ('bunker', 'green', 'fairway', 'tee', 'rough'):
        return g
    if tags.get('natural') in ('wood', 'scrub') or tags.get('landuse') == 'forest':
        return 'wood'
    return None

def render(slug, name, city, bbox, course_rings, features):
    w_lon, s_lat, e_lon, n_lat = bbox

    def to_px(geom):
        return np.array([
            [(p['lon'] - w_lon) / (e_lon - w_lon) * D,
             (n_lat - p['lat']) / (n_lat - s_lat) * D]
            for p in geom], dtype=np.int32)

    layers = {k: [] for k in ['rough', 'wood', 'fairway', 'tee', 'green', 'bunker', 'water', 'path']}
    holes = []
    for el in features:
        if 'geometry' not in el and el.get('type') != 'relation':
            continue
        tags = el.get('tags', {})
        k = kind_of(tags)
        if k == 'hole':
            holes.append({'ref': tags.get('ref'), 'par': tags.get('par'),
                          'geom': el['geometry'], 'pts': to_px(el['geometry'])})
        elif k:
            for ring in ([el['geometry']] if 'geometry' in el else rings_of(el)):
                layers[k].append(to_px(ring))

    img = np.full((D, D, 3), BG, dtype=np.uint8)
    tree = cartoon_tree_mask(f'{slug}_naip.jpg', bbox, course_rings)
    m = (tree > 140)[:, :, None].astype(np.float32)
    img = (img * (1 - m) + np.full_like(img, WOOD) * m).astype(np.uint8)
    deep = cv2.erode((tree > 140).astype(np.uint8) * 255,
                     cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (31, 31)))
    m2 = (deep > 0)[:, :, None].astype(np.float32)
    img = (img * (1 - m2) + np.full_like(img, WOOD_DEEP) * m2).astype(np.uint8)

    for pts in layers['rough']:
        cv2.fillPoly(img, [pts], ROUGH, lineType=cv2.LINE_AA)
    for pts in layers['wood']:
        cv2.fillPoly(img, [pts], WOOD, lineType=cv2.LINE_AA)
    for pts in layers['fairway']:
        cv2.fillPoly(img, [pts], FAIRWAY, lineType=cv2.LINE_AA)
        cv2.polylines(img, [pts], True, WHITE, 3, cv2.LINE_AA)
    for pts in layers['tee']:
        cv2.fillPoly(img, [pts], FAIRWAY, lineType=cv2.LINE_AA)
    for pts in layers['green']:
        cv2.fillPoly(img, [pts], GREEN, lineType=cv2.LINE_AA)
        cv2.polylines(img, [pts], True, GREEN_LINE, 2, cv2.LINE_AA)
    for pts in layers['bunker']:
        cv2.fillPoly(img, [pts], BUNKER, lineType=cv2.LINE_AA)
        cv2.polylines(img, [pts], True, BUNK_LINE, 2, cv2.LINE_AA)
    for pts in layers['water']:
        cv2.fillPoly(img, [pts], WATER, lineType=cv2.LINE_AA)
        cv2.polylines(img, [pts], True, WATER_LINE, 3, cv2.LINE_AA)
    for pts in layers['path']:
        cv2.polylines(img, [pts], False, PATH, 3, cv2.LINE_AA)

    if course_rings:
        mask = np.zeros((D, D), dtype=np.uint8)
        for ring in course_rings:
            cv2.fillPoly(mask, [to_px(ring)], 255)
        mask = cv2.dilate(mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (41, 41)))
        mask = cv2.GaussianBlur(mask, (61, 61), 0)
        mf = (mask.astype(np.float32) / 255.0)[:, :, None]
        img = (img * mf + np.full_like(img, BG_OUT) * (1 - mf)).astype(np.uint8)

    badges = []
    for hl in holes:
        draw_dashed(img, hl['pts'], WHITE, 5)
        draw_arrow(img, hl['pts'], WHITE)
        rs = resample(hl['pts'])
        d = (rs[5] if len(rs) > 5 else rs[-1]) - rs[0]
        n = d / (np.hypot(*d) + 1e-9)
        c = rs[0] + n * 48
        cv2.circle(img, tuple(c.astype(int)), 27, WHITE, -1, cv2.LINE_AA)
        cv2.circle(img, tuple(c.astype(int)), 27, PINE, 4, cv2.LINE_AA)
        badges.append((c, str(hl['ref'] or '')))

    font = ImageFont.truetype(FB, 34)
    pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    dr = ImageDraw.Draw(pil)
    for c, label in badges:
        if label:
            dr.text((float(c[0]), float(c[1])), label, font=font, fill=(14, 59, 44), anchor='mm')
    img = cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)
    out = cv2.resize(img, (SIZE, SIZE), interpolation=cv2.INTER_AREA)
    return out, holes

def scorecard_strip(name, city, holes):
    valid = [h for h in holes if h.get('ref') and str(h['ref']).isdigit() and h.get('par')]
    refs = [int(h['ref']) for h in valid]
    use_table = len(valid) in (9, 18) and len(set(refs)) == len(refs)
    rows_data = sorted(({'n': int(h['ref']), 'par': int(h['par']),
                         'yds': hole_yards(h['geom'])} for h in valid),
                       key=lambda x: x['n']) if use_table else []
    title_h, hdr_h, yds_h, par_h, gap = 60, 34, 42, 36, 12
    banks = [rows_data[:9], rows_data[9:]] if len(rows_data) == 18 else ([rows_data] if rows_data else [])
    H = title_h + sum(hdr_h + yds_h + par_h for _ in banks) + (gap if len(banks) == 2 else 0) + 24
    img = Image.new('RGB', (SIZE, H), (243, 247, 242))
    dr = ImageDraw.Draw(img)
    f_title = ImageFont.truetype(FB, 27)
    f_sub = ImageFont.truetype(FR, 18)
    f_hdr = ImageFont.truetype(FB, 18)
    f_yds = ImageFont.truetype(FB, 21)
    f_par = ImageFont.truetype(FR, 19)
    dr.text((36, 16), name[:52], font=f_title, fill=(14, 59, 44))
    tot_yds = sum(r['yds'] for r in rows_data)
    tot_par = sum(r['par'] for r in rows_data)
    sub = (f'Par {tot_par}  ·  {tot_yds:,} yards  ·  ' if rows_data else '') + (city or 'Michigan')
    sw = dr.textlength(sub, font=f_sub)
    dr.text((SIZE - 36 - sw, 24), sub, font=f_sub, fill=(47, 93, 58))

    margin, label_w, agg_w, tot_w = 36, 80, 106, 116
    hole_w = (SIZE - 2 * margin - label_w - agg_w - tot_w) // 9
    y = title_h
    for bi, nine in enumerate(banks):
        agg_lbl = 'OUT' if bi == 0 and len(banks) == 2 else 'IN' if bi == 1 else 'TOT'
        show_tot = (bi == len(banks) - 1) and len(banks) == 2
        xs = [margin]
        for w in [label_w] + [hole_w] * 9 + [agg_w, tot_w]:
            xs.append(xs[-1] + w)
        agg_y, agg_p = sum(r['yds'] for r in nine), sum(r['par'] for r in nine)
        rows = [('HOLE', [str(r['n']) for r in nine], agg_lbl, 'TOT' if show_tot else '',
                 hdr_h, (14, 59, 44), (255, 255, 255), f_hdr),
                ('YDS', [str(r['yds']) for r in nine], f'{agg_y:,}',
                 f'{tot_yds:,}' if show_tot else '', yds_h, (255, 255, 255), (14, 59, 44), f_yds),
                ('PAR', [str(r['par']) for r in nine], str(agg_p),
                 str(tot_par) if show_tot else '', par_h, (227, 238, 224), (47, 93, 58), f_par)]
        for label, cells, agg, tot, rh, bg_c, fg, fnt in rows:
            vals = [label] + cells + [agg, tot]
            while len(vals) < 12:
                vals.append('')
            for i, val in enumerate(vals):
                cell_bg = (214, 229, 209) if (i >= 10 and bg_c != (14, 59, 44)) else bg_c
                dr.rectangle([xs[i], y, xs[i + 1], y + rh], fill=cell_bg, outline=(203, 214, 199), width=1)
                if val:
                    dr.text(((xs[i] + xs[i + 1]) / 2, y + rh / 2), val,
                            font=f_hdr if (i >= 10 and fnt is f_par) else fnt,
                            fill=(255, 255, 255) if bg_c == (14, 59, 44) else fg, anchor='mm')
            y += rh
        y += gap
    return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)

def process(course):
    slug = re.sub(r'[^a-z0-9]+', '-', course['name'].lower()).strip('-')
    print(f"== {course['name']} ({course['tier']}) -> {slug}", flush=True)
    q = (f"[out:json][timeout:25];{course['osm_type']}({course['osm_id']});out geom;")
    bd = overpass(q, f'{slug}_boundary.json')
    el = bd['elements'][0]
    rings = rings_of(el)
    lats = [p['lat'] for r in rings for p in r]
    lons = [p['lon'] for r in rings for p in r]
    city = el.get('tags', {}).get('addr:city', '')
    pad = 0.06
    s_lat, n_lat = min(lats), max(lats)
    w_lon, e_lon = min(lons), max(lons)
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
    bbox = (w_lon, s_lat, e_lon, n_lat)

    fetch(NAIP.format(w=w_lon, s=s_lat, e=e_lon, n=n_lat), f'{slug}_naip.jpg')
    time.sleep(1)
    bb = f'({s_lat},{w_lon},{n_lat},{e_lon})'
    q2 = ('[out:json][timeout:30];('
          f'way["golf"~"^(fairway|green|bunker|tee|rough|hole|water_hazard|lateral_water_hazard|cartpath)$"]{bb};'
          f'way["natural"~"^(water|wood|scrub)$"]{bb};'
          f'way["landuse"="forest"]{bb};'
          f'way["highway"~"^(path|track|service|footway)$"]{bb};'
          ');out geom;')
    feats = overpass(q2, f'{slug}_features.json')['elements']
    course_map, holes = render(slug, course['name'], city, bbox, rings, feats)
    strip = scorecard_strip(course['name'], city, holes)
    card = np.vstack([course_map, strip])
    cv2.imwrite(f'{slug}_card.png', card)
    print(f'   card written: {slug}_card.png  features={len(feats)} holes={len(holes)}', flush=True)
    return slug

def pilot_list():
    manifest = json.load(open('mi_manifest.json'))
    by_name = {m['name']: m for m in manifest}
    pilot_names = ['Arcadia Bluffs - South Course', 'Lost Dunes', 'Egypt Valley Country Club',
                   'Oakland Hills Country Club', 'Northville Hills Golf Club', 'TPC Michigan']
    pilot = [by_name[n] for n in pilot_names if n in by_name]
    partials = sorted((m for m in manifest if m['tier'] == 'partial'), key=lambda m: -m['fairways'])
    rasters = [m for m in manifest if m['tier'] == 'raster' and m['holes_tag'] == '18']
    if partials:
        pilot.append(partials[0])
    if rasters:
        pilot.append(rasters[0])
    return pilot

if __name__ == '__main__':
    import sys
    pilot = pilot_list()
    if len(sys.argv) > 1:
        i = int(sys.argv[1])
        print(f'[{i + 1}/{len(pilot)}]', flush=True)
        process(pilot[i])
    else:
        for c in pilot:
            try:
                process(c)
            except Exception as ex:
                print('   FAILED:', c['name'], ex, flush=True)
        print('DONE', flush=True)
