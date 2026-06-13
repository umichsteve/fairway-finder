"""Every18 cost crawler — derive cost (1-5) from each course's own published green fees.

Stages (resumable; cache/ persists fetched pages):
  python3 cost_crawler.py inventory [--states MI,OH]   # course websites from OSM -> course_sites.csv
  python3 cost_crawler.py crawl [--limit N]            # fetch homepage + rates page per course -> cache/
  python3 cost_crawler.py extract                      # parse cached pages -> cost_us.csv

Only primary sources: each course's own website. No aggregators/competitors.
Respects robots.txt, 1 req/s, identifies as Every18 with contact.
"""
import argparse
import csv
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.robotparser

CACHE = 'cost_cache'
UA = 'Every18Bot/0.1 (+https://every18.com/about; steve.demaagd@gmail.com)'
OVERPASS = ['https://overpass-api.de/api/interpreter',
            'https://overpass.private.coffee/api/interpreter',
            'https://overpass.kumi.systems/api/interpreter']

STATES = ['AL','AK','AZ','AR','CA','CO','CT','DE','FL','GA','HI','ID','IL','IN','IA','KS',
          'KY','LA','ME','MD','MA','MI','MN','MS','MO','MT','NE','NV','NH','NJ','NM','NY',
          'NC','ND','OH','OK','OR','PA','RI','SC','SD','TN','TX','UT','VT','VA','WA','WV',
          'WI','WY','DC']

RATES_HINTS = ['rate', 'fee', 'green-fee', 'greenfee', 'pricing', 'price', 'tee-time', 'golf-rate']
FEE_CONTEXT = re.compile(r'(18|eighteen|all\s*day|weekend|weekday|green\s*fee|w/?\s*cart|riding|walking)',
                         re.I)
PRICE_RE = re.compile(r'\$\s?(\d{1,4})(?:\.\d{2})?')

# ---------- shared ----------

def http_get(url, out_path, timeout=20):
    """Fetch via curl (CI has network). Returns final URL or None."""
    import subprocess
    os.makedirs(os.path.dirname(out_path) or '.', exist_ok=True)
    r = subprocess.run(
        ['curl', '-sL', '-m', str(timeout), '-A', UA, '-w', '%{url_effective}',
         '-o', out_path, url],
        capture_output=True, text=True)
    final = r.stdout.strip() or url
    if os.path.exists(out_path) and os.path.getsize(out_path) > 200:
        return final
    return None

def overpass(query, out_file):
    import subprocess
    if os.path.exists(out_file):
        try:
            return json.load(open(out_file))
        except Exception:
            pass
    for url in OVERPASS:
        subprocess.run(['curl', '-s', '-m', '30', '-A', UA, url,
                        '--data-urlencode', f'data={query}', '-o', out_file])
        try:
            return json.load(open(out_file))
        except Exception:
            time.sleep(5)
    raise RuntimeError('overpass failed')

def slugify(state, name):
    return f"{state.lower()}-" + re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')[:60]

def robots_ok(base, path):
    try:
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(urllib.parse.urljoin(base, '/robots.txt'))
        rp.read()
        return rp.can_fetch(UA, path)
    except Exception:
        return True  # absent/broken robots = allowed

# ---------- inventory ----------

def cmd_inventory(states):
    os.makedirs(CACHE, exist_ok=True)
    rows = []
    for st in states:
        f = f'{CACHE}/sites_{st}.json'
        q = (f'[out:json][timeout:40];area["ISO3166-2"="US-{st}"][admin_level=4]->.a;'
             '(way["leisure"="golf_course"]["name"](area.a);'
             'relation["leisure"="golf_course"]["name"](area.a););out center tags;')
        d = overpass(q, f)
        for e in d.get('elements', []):
            t = e.get('tags', {})
            site = t.get('website') or t.get('contact:website') or t.get('url')
            if not site or t.get('access') == 'private':
                continue
            if not site.startswith('http'):
                site = 'http://' + site
            rows.append({'slug': slugify(st, t['name']), 'name': t['name'], 'state': st,
                         'website': site})
        time.sleep(2)
        print(f'{st}: {sum(1 for r in rows if r["state"]==st)} public courses with sites', flush=True)
    seen, uniq = set(), []
    for r in rows:
        if r['slug'] in seen:
            continue
        seen.add(r['slug'])
        uniq.append(r)
    with open('course_sites.csv', 'w', newline='') as fo:
        w = csv.DictWriter(fo, fieldnames=['slug', 'name', 'state', 'website'])
        w.writeheader()
        w.writerows(uniq)
    print(f'{len(uniq)} course websites -> course_sites.csv', flush=True)

# ---------- crawl ----------

def find_rates_link(html, base):
    links = re.findall(r'href=["\']([^"\']+)["\']', html, re.I)
    scored = []
    for href in links:
        low = href.lower()
        if MEMBERSHIP_URL.search(low):  # never follow a membership/season/event page as "rates"
            continue
        score = sum(2 if h in low else 0 for h in RATES_HINTS)
        if score:
            scored.append((score, urllib.parse.urljoin(base, href)))
    scored.sort(reverse=True)
    return scored[0][1] if scored else None

def cmd_crawl(limit):
    os.makedirs(CACHE, exist_ok=True)
    sites = list(csv.DictReader(open('course_sites.csv')))
    if limit:
        sites = sites[:limit]
    done = 0
    for s in sites:
        slug = s['slug']
        home = f'{CACHE}/{slug}_home.html'
        rates = f'{CACHE}/{slug}_rates.html'
        if os.path.exists(home):
            done += 1
            continue
        try:
            base = s['website']
            parsed = urllib.parse.urlparse(base)
            if not robots_ok(base, parsed.path or '/'):
                open(f'{CACHE}/{slug}.skip', 'w').write('robots')
                continue
            final = http_get(base, home)
            if not final:
                continue
            html = open(home, encoding='utf-8', errors='ignore').read()
            link = find_rates_link(html, final)
            if link and robots_ok(final, urllib.parse.urlparse(link).path):
                time.sleep(1)
                http_get(link, rates)
                open(f'{CACHE}/{slug}.rateurl', 'w').write(link)
            done += 1
            if done % 25 == 0:
                print(f'{done}/{len(sites)} crawled', flush=True)
        except Exception as ex:
            print(f'skip {slug}: {ex}', flush=True)
        time.sleep(1)
    print(f'crawl done: {done}', flush=True)

# ---------- extract ----------

GOLF_WORDS = re.compile(r'\b(golf|tee|hole|fairway|green\s*fee|course|clubhouse)\b', re.I)
SPAM_WORDS = re.compile(r'\b(homework|essay|casino|viagra|loan|crypto)\b', re.I)

def text_of(html):
    html = re.sub(r'<script.*?</script>', ' ', html, flags=re.S | re.I)
    html = re.sub(r'<style.*?</style>', ' ', html, flags=re.S | re.I)
    return re.sub(r'<[^>]+>', ' ', html)

EIGHTEEN = re.compile(r'\b(18|eighteen)\b', re.I)
DISCOUNT = re.compile(r'\b(jr|junior|sr|senior|twilight|military|kid|child|youth|league|9\s*hole|nine)\b',
                      re.I)
# membership/season pricing must never be read as a green fee
MEMBERSHIP = re.compile(r'\b(member|membership|season\s*pass|annual|initiation|dues|'
                        r'/year|per\s*year|/yr|punch\s*card|gift\s*card|wedding|banquet|'
                        r'event|outing\s*package|stay\s*and\s*play|monthly)\b', re.I)
MEMBERSHIP_URL = re.compile(r'(member|season-pass|annual|dues|gift|wedding|banquet|package)', re.I)

def _median(xs):
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2

def extract_fee(html):
    """Return (peak_fee, confidence) or (None, reason).
    Peak = highest standard 18-hole green fee (discount rows excluded), junk-filtered."""
    text = text_of(html)
    if SPAM_WORDS.search(text) and not GOLF_WORDS.search(text):
        return None, 'hijacked'
    if not GOLF_WORDS.search(text):
        return None, 'not-golf'

    eighteen, contextual = [], []
    for m in PRICE_RE.finditer(text):
        val = int(m.group(1))
        if val < 8 or val > 600:  # >$600 single green fee is implausible outside a handful of resorts
            continue
        # labels precede the price in fee tables; look mostly behind to avoid bleed from the next row
        window = text[max(0, m.start() - 75):m.end() + 12]
        if not FEE_CONTEXT.search(window):
            continue
        if MEMBERSHIP.search(window):  # membership/season/event pricing — not a round
            continue
        if DISCOUNT.search(window):
            continue
        contextual.append(val)
        if EIGHTEEN.search(window):
            eighteen.append(val)

    pool = eighteen or contextual
    if not pool:
        return None, 'no-fee'  # no clean green-fee signal; do NOT guess (was the 'low' bug source)

    med = _median(pool)
    clean = [v for v in pool if v <= 3 * med] or pool  # drop residual junk spikes
    conf = 'high' if eighteen else 'medium'
    return max(clean), conf

def band(fee):
    if fee < 35:
        return 1
    if fee < 60:
        return 2
    if fee < 100:
        return 3
    if fee < 175:
        return 4
    return 5

def stale_year(html):
    yrs = [int(y) for y in re.findall(r'(?:©|copyright|&copy;)\s*(20\d{2})', html, re.I)]
    return max(yrs) if yrs else None

def cmd_extract():
    sites = {s['slug']: s for s in csv.DictReader(open('course_sites.csv'))}
    out = []
    for slug, s in sites.items():
        rates = f'{CACHE}/{slug}_rates.html'
        home = f'{CACHE}/{slug}_home.html'
        path = rates if os.path.exists(rates) else home
        if not os.path.exists(path):
            continue
        html = open(path, encoding='utf-8', errors='ignore').read()
        fee, conf = extract_fee(html)
        if fee is None:
            continue
        src = open(f'{CACHE}/{slug}.rateurl').read() if os.path.exists(f'{CACHE}/{slug}.rateurl') else s['website']
        out.append({'slug': slug, 'name': s['name'], 'state': s['state'],
                    'peak_fee': fee, 'cost': band(fee), 'confidence': conf,
                    'source': src, 'site_year': stale_year(html) or ''})
    with open('cost_us.csv', 'w', newline='') as fo:
        w = csv.DictWriter(fo, fieldnames=['slug', 'name', 'state', 'peak_fee', 'cost',
                                           'confidence', 'source', 'site_year'])
        w.writeheader()
        w.writerows(out)
    hi = sum(1 for r in out if r['confidence'] == 'high')
    print(f'extracted cost for {len(out)} courses ({hi} high-confidence) -> cost_us.csv', flush=True)

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('stage', choices=['inventory', 'crawl', 'extract'])
    ap.add_argument('--states')
    ap.add_argument('--limit', type=int)
    a = ap.parse_args()
    if a.stage == 'inventory':
        cmd_inventory(a.states.split(',') if a.states else STATES)
    elif a.stage == 'crawl':
        cmd_crawl(a.limit)
    else:
        cmd_extract()
