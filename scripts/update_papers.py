#!/usr/bin/env python3
"""
Refresh data/papers.json from three sources:

  ff  Freedom Forum   (frontpages.freedomforum.org; date-stamped image URLs)
  fp  FrontPages.com  (per-day hashed image URLs -> recorded daily by this script)
  kk  Kiosko.net      (date-stamped image URLs)

Strategies per source:
  ff: homepage + gallery HTML, the same routes requested as a Next.js RSC
      payload, robots.txt-discovered sitemaps plus common sitemap paths,
      expanding sitemap indexes. Slug pattern tolerates JSON-escaped slashes.
  fp: per-country listing pages (main grid only, split at first <h2>).
  kk: per-country landing pages PLUS their linked geo/*.html region pages.

All requests use browser-like headers. If a page comes back blocked
(401/403/429/503) it is retried through the r.jina.ai text mirror, whose
markdown output preserves the absolute URLs these parsers look for.

Merging is conservative (existing entries survive a bad scrape day) and
cross-source dedupe keeps ff > fp > kk. Diagnostics go to stderr so the
Actions log shows exactly what each page returned.

No third-party dependencies; runs on stock Python 3.9+.
"""

import html as htmllib
import json
import re
import ssl
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import unquote, urljoin

try:
    from zoneinfo import ZoneInfo
    DENVER = ZoneInfo("America/Denver")
except Exception:
    DENVER = None

DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "papers.json"
DELAY = 0.25    # politeness between requests
RETRY_429 = 20  # seconds to back off once when rate-limited

# kiosko.net's TLS uses a legacy signature algorithm that OpenSSL 3 rejects
# by default (WRONG_SIGNATURE_TYPE); lowering the security level fixes it.
SSL_CTX = ssl.create_default_context()
try:
    SSL_CTX.set_ciphers("DEFAULT:@SECLEVEL=1")
except ssl.SSLError:
    pass

BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

def note(msg):
    print(msg, file=sys.stderr)

def _request(url, headers=None, timeout=45):
    h = dict(BROWSER_HEADERS)
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as r:
        return r.read().decode("utf-8", errors="replace")

def _jina(url):
    txt = _request("https://r.jina.ai/" + url)
    note(f"    jina mirror ok for {url} ({len(txt)} chars)")
    return txt

def get(url, headers=None, mirror=True):
    """Fetch with browser headers; on block, retry via the r.jina.ai mirror."""
    try:
        txt = _request(url, headers)
        time.sleep(DELAY)
        return txt
    except urllib.error.HTTPError as e:
        note(f"    {url} -> HTTP {e.code}")
        if e.code == 429:
            note(f"    backing off {RETRY_429}s and retrying once…")
            time.sleep(RETRY_429)
            try:
                txt = _request(url, headers)
                time.sleep(DELAY)
                return txt
            except Exception as e2:
                note(f"    429 retry failed: {e2}")
        if mirror and e.code in (401, 403, 406, 429, 503):
            try:
                return _jina(url)
            except Exception as e2:
                note(f"    jina mirror failed too: {e2}")
        raise
    except Exception as e:
        note(f"    {url} -> {e}")
        if mirror:
            try:
                return _jina(url)
            except Exception as e2:
                note(f"    jina mirror failed too: {e2}")
        raise


# ----------------------------------------------------------------- regions ---

US_STATES = {
    "al": "Alabama", "ak": "Alaska", "az": "Arizona", "ar": "Arkansas",
    "ca": "California", "co": "Colorado", "ct": "Connecticut", "de": "Delaware",
    "dc": "District of Columbia", "fl": "Florida", "ga": "Georgia",
    "hi": "Hawaii", "id": "Idaho", "il": "Illinois", "in": "Indiana",
    "ia": "Iowa", "ks": "Kansas", "ky": "Kentucky", "la": "Louisiana",
    "me": "Maine", "md": "Maryland", "ma": "Massachusetts", "mi": "Michigan",
    "mn": "Minnesota", "ms": "Mississippi", "mo": "Missouri", "mt": "Montana",
    "ne": "Nebraska", "nv": "Nevada", "nh": "New Hampshire", "nj": "New Jersey",
    "nm": "New Mexico", "ny": "New York", "nc": "North Carolina",
    "nd": "North Dakota", "oh": "Ohio", "ok": "Oklahoma", "or": "Oregon",
    "pa": "Pennsylvania", "ri": "Rhode Island", "sc": "South Carolina",
    "sd": "South Dakota", "tn": "Tennessee", "tx": "Texas", "ut": "Utah",
    "vt": "Vermont", "va": "Virginia", "wa": "Washington",
    "wv": "West Virginia", "wi": "Wisconsin", "wy": "Wyoming",
    "pr": "Puerto Rico", "gu": "Guam", "vi": "U.S. Virgin Islands",
}

WEUROPE = {
    "united kingdom", "england", "scotland", "wales", "northern ireland",
    "ireland", "france", "germany", "spain", "portugal", "italy",
    "netherlands", "belgium", "luxembourg", "switzerland", "austria",
    "denmark", "norway", "sweden", "finland", "iceland", "greece",
    "malta", "monaco", "san marino", "vatican", "andorra", "cyprus",
}

def region_for_country(country):
    c = (country or "").lower()
    if c in ("united states", "us", "usa"): return "us"
    if c == "canada": return "canada"
    if c == "mexico": return "mexico"
    if c in WEUROPE: return "weurope"
    return "world"

FF_COUNTRIES = {
    "can": "Canada", "mex": "Mexico",
    "uk": "United Kingdom", "gbr": "United Kingdom", "eng": "England",
    "sco": "Scotland", "wal": "Wales", "nir": "Northern Ireland",
    "irl": "Ireland", "ire": "Ireland", "fra": "France",
    "ger": "Germany", "deu": "Germany", "esp": "Spain", "spa": "Spain",
    "prt": "Portugal", "por": "Portugal", "ita": "Italy",
    "nld": "Netherlands", "ned": "Netherlands", "bel": "Belgium",
    "lux": "Luxembourg", "che": "Switzerland", "sui": "Switzerland",
    "swi": "Switzerland", "aut": "Austria", "dnk": "Denmark",
    "den": "Denmark", "nor": "Norway", "swe": "Sweden", "fin": "Finland",
    "isl": "Iceland", "ice": "Iceland", "grc": "Greece", "gre": "Greece",
    "mlt": "Malta", "mco": "Monaco", "and": "Andorra", "cyp": "Cyprus",
    "arg": "Argentina", "aus": "Australia", "bra": "Brazil", "bol": "Bolivia",
    "chl": "Chile", "chi": "Chile", "chn": "China", "col": "Colombia",
    "cri": "Costa Rica", "cze": "Czechia", "ecu": "Ecuador", "egy": "Egypt",
    "est": "Estonia", "hkg": "Hong Kong", "hun": "Hungary", "ind": "India",
    "idn": "Indonesia", "isr": "Israel", "jam": "Jamaica", "jpn": "Japan",
    "ken": "Kenya", "kor": "South Korea", "kwt": "Kuwait", "lbn": "Lebanon",
    "ltu": "Lithuania", "lva": "Latvia", "mys": "Malaysia", "nga": "Nigeria",
    "nzl": "New Zealand", "omn": "Oman", "pak": "Pakistan", "pan": "Panama",
    "per": "Peru", "phl": "Philippines", "phi": "Philippines",
    "pol": "Poland", "qat": "Qatar", "rou": "Romania", "rom": "Romania",
    "rus": "Russia", "sau": "Saudi Arabia", "sgp": "Singapore",
    "sin": "Singapore", "svk": "Slovakia", "svn": "Slovenia",
    "tha": "Thailand", "tur": "Turkey", "twn": "Taiwan",
    "uae": "United Arab Emirates", "ukr": "Ukraine", "ury": "Uruguay",
    "uru": "Uruguay", "ven": "Venezuela", "vnm": "Vietnam", "vie": "Vietnam",
    "zaf": "South Africa", "saf": "South Africa", "zwe": "Zimbabwe",
}

US_STATE_BY_NAME = {
    "new york times": "New York", "los angeles times": "California",
    "washington post": "District of Columbia", "new york post": "New York",
    "newsday": "New York", "boston globe": "Massachusetts",
    "arizona republic": "Arizona", "houston chronicle": "Texas",
    "san francisco chronicle": "California", "mercury news": "California",
    "ny daily news": "New York", "new york daily news": "New York",
    "chicago tribune": "Illinois", "washington times": "District of Columbia",
    "miami herald": "Florida", "star tribune": "Minnesota",
    "minnesota star tribune": "Minnesota", "dallas morning news": "Texas",
    "chicago sun times": "Illinois", "charlotte observer": "North Carolina",
    "philadelphia inquirer": "Pennsylvania", "kansas city star": "Missouri",
    "oklahoman": "Oklahoma", "sacramento bee": "California",
    "milwaukee journal sentinel": "Wisconsin", "palm beach post": "Florida",
    "columbus dispatch": "Ohio", "austin american statesman": "Texas",
    "las vegas review journal": "Nevada", "fort worth star telegram": "Texas",
    "commercial appeal": "Tennessee", "la opinion": "California",
    "east bay times": "California", "detroit free press": "Michigan",
    "el nuevo herald": "Florida", "atlanta journal constitution": "Georgia",
    "el diario ny": "New York", "san diego union tribune": "California",
    "oakland tribune": "California", "seattle times": "Washington",
    "denver post": "Colorado", "tampa bay times": "Florida",
    "baltimore sun": "Maryland", "orlando sentinel": "Florida",
    "pittsburgh post gazette": "Pennsylvania", "st louis post dispatch": "Missouri",
    "omaha world herald": "Nebraska", "el paso times": "Texas",
}

def norm_name(name):
    s = unicodedata.normalize("NFKD", name)
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    if s.startswith("the "):
        s = s[4:]
    return s

def strip_tags(s):
    return htmllib.unescape(re.sub(r"<[^>]+>", " ", s)).strip()


# ------------------------------------------------------- source: freedomforum

FF_BASE = "https://frontpages.freedomforum.org"
# tolerate JSON-escaped slashes (\/newspapers\/...) inside script payloads
FF_SLUG = re.compile(r"(?:\\/|/)newspapers(?:\\/|/)([a-z0-9_]+)-([^\"'<>\s\\)?#&]+)")
LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>")

def _ff_harvest(text, out, label):
    n0 = len(out)
    for code, raw in FF_SLUG.findall(text or ""):
        name = re.sub(r"\s+", " ", unquote(raw).replace("_", " ")).strip()
        if not name or code in out:
            continue
        prefix = code.split("_", 1)[0] if "_" in code else code
        if "_" in code and prefix in US_STATES:
            country, state = "United States", US_STATES[prefix]
        elif prefix in FF_COUNTRIES:
            country, state = FF_COUNTRIES[prefix], None
        elif "_" not in code:
            country, state = "United States", None
        else:
            country, state = prefix.upper(), None
        out[code] = dict(uid=f"ff:{code}", source="ff", id=code, name=name,
                         country=country, state=state,
                         region=region_for_country(country), img=None)
    note(f"  ff {label}: +{len(out)-n0} (total {len(out)})")

def scrape_freedomforum():
    out = {}
    for url in (FF_BASE + "/", FF_BASE + "/gallery"):
        for hdr, label in ((None, "html"), ({"RSC": "1"}, "rsc")):
            try:
                _ff_harvest(get(url, headers=hdr), out, f"{url} [{label}]")
            except Exception as exc:
                note(f"  ff skip {url} [{label}]: {exc}")
    # the gallery is client-rendered; the jina mirror renders JS, so ask it
    # for the full gallery regardless of whether the direct fetch "worked"
    try:
        _ff_harvest(_jina(FF_BASE + "/gallery"), out, "gallery [jina-rendered]")
    except Exception as exc:
        note(f"  ff jina gallery: {exc}")
    # sitemap discovery: robots.txt first, then common paths
    sitemap_urls = []
    try:
        robots = get(FF_BASE + "/robots.txt")
        sitemap_urls += re.findall(r"(?im)^sitemap:\s*(\S+)", robots)
        note(f"  ff robots.txt lists {len(sitemap_urls)} sitemap(s)")
    except Exception as exc:
        note(f"  ff robots.txt: {exc}")
    sitemap_urls += [FF_BASE + p for p in
                     ("/sitemap.xml", "/sitemap-0.xml", "/sitemap_index.xml",
                      "/server-sitemap.xml")]
    seen, queue = set(), list(dict.fromkeys(sitemap_urls))
    while queue and len(seen) < 30:
        sm = queue.pop(0)
        if sm in seen:
            continue
        seen.add(sm)
        try:
            xml = get(sm)
        except Exception:
            continue
        _ff_harvest(xml, out, sm)
        if "<sitemap>" in xml:                    # sitemap index -> children
            for child in LOC_RE.findall(xml):
                if "freedomforum" in child and child not in seen:
                    queue.append(child)
    print(f"ff: {len(out)} papers")
    return list(out.values())


# ------------------------------------------------------- source: frontpages.com

FP_BASE = "https://www.frontpages.com"
FP_COUNTRIES = {
    "us": "United States", "canada": "Canada", "mexico": "Mexico",
    "uk": "United Kingdom", "ireland": "Ireland", "france": "France",
    "germany": "Germany", "spain": "Spain", "portugal": "Portugal",
    "italy": "Italy", "netherlands": "Netherlands", "belgium": "Belgium",
    "switzerland": "Switzerland", "austria": "Austria", "denmark": "Denmark",
    "norway": "Norway", "sweden": "Sweden", "finland": "Finland",
    "greece": "Greece", "malta": "Malta", "san-marino": "San Marino",
    "vatican": "Vatican", "poland": "Poland", "croatia": "Croatia",
    "romania": "Romania", "slovenia": "Slovenia", "albania": "Albania",
    "turkey": "Turkey", "israel": "Israel", "palestine": "Palestine",
    "jordan": "Jordan", "saudi-arabia": "Saudi Arabia", "qatar": "Qatar",
    "uae": "United Arab Emirates", "india": "India", "pakistan": "Pakistan",
    "bangladesh": "Bangladesh", "china": "China", "hong-kong": "Hong Kong",
    "taiwan": "Taiwan", "japan": "Japan", "south-korea": "South Korea",
    "thailand": "Thailand", "vietnam": "Vietnam", "malaysia": "Malaysia",
    "singapore": "Singapore", "indonesia": "Indonesia",
    "philippines": "Philippines", "australia": "Australia",
    "new-zealand": "New Zealand", "argentina": "Argentina", "brazil": "Brazil",
    "nigeria": "Nigeria", "kenya": "Kenya", "south-africa": "South Africa",
}
FP_ANCHOR = re.compile(
    r'<a[^>]+href="(?:https://www\.frontpages\.com)?/([a-z0-9\-]+)/"[^>]*>(.*?)</a>',
    re.S)
# markdown-mirror form: [**Name** ...](https://www.frontpages.com/slug/)
FP_MD_ANCHOR = re.compile(r"\[\*\*(.+?)\*\*.*?\]\(https://www\.frontpages\.com/([a-z0-9\-]+)/\)")
FP_IMG = re.compile(r'([^\s"\'()<>\]]*?/g/(\d{4})/(\d{2})/(\d{2})/([a-z0-9\-]+)-[0-9a-z]+\.[a-z.]+)')
FP_NAME = re.compile(r"<(?:b|strong)[^>]*>(.*?)</(?:b|strong)>", re.S)
FP_OG  = re.compile(r'(?:property|name)="og:image"[^>]*?content="([^"]+)"')
FP_OG2 = re.compile(r'content="([^"]+)"[^>]*?(?:property|name)="og:image"')
FP_SKIP = {"sports-newspapers", "financial-newspapers", "world-newspapers",
           "newspaper-list", "uk-newspapers", "us-newspapers"}
FP_CUT = re.compile(r"<h2|SPORTS Newspapers|WORLD Newspapers", re.I)

def _fp_clean(name):
    name = re.sub(r"\s*(Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)day,.*$", "", name)
    return name.strip()

def scrape_frontpages():
    out = {}
    for cslug, country in FP_COUNTRIES.items():
        url = f"{FP_BASE}/{cslug}-newspapers/"
        try:
            page = get(url)
        except Exception as exc:
            note(f"  fp skip {url}: {exc}")
            continue
        m = FP_CUT.search(page)
        main = page[:m.start()] if m else page
        imgs = {}
        for full, y, mo, d, slug in FP_IMG.findall(main):
            u = full if full.startswith("http") else FP_BASE + (full if full.startswith("/") else "/" + full)
            imgs.setdefault(slug, u)
        found = 0
        pairs = [(slug, None, inner) for slug, inner in FP_ANCHOR.findall(main)]
        pairs += [(slug, name, "") for name, slug in FP_MD_ANCHOR.findall(main)]
        for slug, mdname, inner in pairs:
            if slug in FP_SKIP or slug.endswith("-newspapers") \
               or slug.endswith("-sports") or slug.endswith("-sport") or slug in out:
                continue
            if mdname:
                name = _fp_clean(strip_tags(mdname))
            else:
                nm = FP_NAME.search(inner)
                name = _fp_clean(strip_tags(nm.group(1) if nm else inner))
            if not name:
                continue
            state = US_STATE_BY_NAME.get(norm_name(name)) if country == "United States" else None
            out[slug] = dict(uid=f"fp:{slug}", source="fp", id=slug, name=name,
                             country=country, state=state,
                             region=region_for_country(country),
                             img=imgs.get(slug))
            found += 1
        note(f"  fp {url}: +{found} papers, {len(imgs)} images "
             f"({len(page)} chars{'' if found else ' — first 200: ' + repr(page[:200])})")
    # --- second pass: per-paper og:image (server-rendered, has the day's URL)
    now = datetime.now(DENVER) if DENVER else datetime.utcnow() - timedelta(hours=6)
    today_path = now.strftime("%Y/%m/%d")
    def img_day(u):
        m = re.search(r"/g/(\d{4}/\d{2}/\d{2})/", u or "")
        return m.group(1) if m else None
    need = [s for s, p in out.items() if img_day(p.get("img")) != today_path]
    note(f"  fp og:image pass: {len(need)} paper pages to check")
    got = 0
    for slug in need[:450]:
        try:
            page = get(f"{FP_BASE}/{slug}/")
        except Exception:
            continue
        m = FP_OG.search(page) or FP_OG2.search(page)
        img = None
        if m and "/g/" in m.group(1):
            img = m.group(1)
        else:
            m2 = FP_IMG.search(page)
            if m2:
                u0 = m2.group(1)
                img = u0 if u0.startswith("http") else \
                      FP_BASE + (u0 if u0.startswith("/") else "/" + u0)
        if img:
            out[slug]["img"] = img
            got += 1
    note(f"  fp og:image found for {got}/{len(need)}")
    print(f"fp: {len(out)} papers")
    return list(out.values())


# ------------------------------------------------------------ source: kiosko

KK_BASE = "https://en.kiosko.net"
KK_COUNTRIES = {
    "us": "United States", "ca": "Canada", "mx": "Mexico",
    "uk": "United Kingdom", "ie": "Ireland", "fr": "France", "de": "Germany",
    "es": "Spain", "pt": "Portugal", "it": "Italy", "nl": "Netherlands",
    "be": "Belgium", "ch": "Switzerland", "at": "Austria", "dk": "Denmark",
    "no": "Norway", "se": "Sweden", "fi": "Finland", "gr": "Greece",
    "pl": "Poland", "cz": "Czechia", "ru": "Russia", "tr": "Turkey",
    "il": "Israel", "jp": "Japan", "cn": "China", "in": "India",
    "au": "Australia", "nz": "New Zealand", "ar": "Argentina",
    "br": "Brazil", "cl": "Chile", "co": "Colombia", "pe": "Peru",
    "ve": "Venezuela", "uy": "Uruguay", "py": "Paraguay", "bo": "Bolivia",
    "ec": "Ecuador", "cr": "Costa Rica", "za": "South Africa",
}
KK_ANCHOR = re.compile(
    r'<a\b[^>]*href="[^"]*?\bnp/([a-z0-9_\-]+)\.html"[^>]*>(.*?)</a>', re.S)
KK_TITLE = re.compile(r'title="([^"]*)"')
KK_GEO = re.compile(r'href="([^"]*?\bgeo/[^"]+\.html)"')

def _kk_harvest(page, cc, country, out):
    n0 = len(out)
    for m in KK_ANCHOR.finditer(page):
        code, inner = m.group(1), m.group(2)
        kid = f"{cc}/{code}"
        if kid in out:
            continue
        opening = m.group(0).split(">", 1)[0]
        t = KK_TITLE.search(opening)
        alt = re.search(r'alt="([^"]{3,})"', inner)
        name = strip_tags(t.group(1) if t else "") \
            or (strip_tags(alt.group(1)) if alt else "") \
            or strip_tags(inner)
        name = re.sub(r"\s*\(.*?\)\s*$", "", name)
        name = re.sub(r"\bnewspaper\b\.?", "", name, flags=re.I).strip(" .-")
        if not name:
            name = code.replace("_", " ").title()
        state = US_STATE_BY_NAME.get(norm_name(name)) if country == "United States" else None
        out[kid] = dict(uid=f"kk:{kid}", source="kk", id=kid, name=name,
                        country=country, state=state,
                        region=region_for_country(country), img=None)
    return len(out) - n0

def scrape_kiosko():
    out = {}
    for cc, country in KK_COUNTRIES.items():
        url = f"{KK_BASE}/{cc}/"
        try:
            page = get(url)
        except Exception as exc:
            note(f"  kk skip {url}: {exc}")
            continue
        n = _kk_harvest(page, cc, country, out)
        # region pages (geo/*.html) carry the rest of large countries' papers
        geos = list(dict.fromkeys(KK_GEO.findall(page)))[:40]
        for g in geos:
            gu = urljoin(url, g)
            try:
                n += _kk_harvest(get(gu), cc, country, out)
            except Exception as exc:
                note(f"  kk skip {gu}: {exc}")
        note(f"  kk {url}: +{n} papers ({len(geos)} geo pages)")
    print(f"kk: {len(out)} papers")
    return list(out.values())


# -------------------------------------------------------------------- main --

REGION_ORDER = {"us": 0, "canada": 1, "mexico": 2, "weurope": 3, "world": 4}
SRC_PRIORITY = {"ff": 0, "fp": 1, "kk": 2}

def main():
    print("update_papers v4")
    scraped = scrape_freedomforum() + scrape_frontpages() + scrape_kiosko()

    existing = {}
    if DATA_FILE.exists():
        try:
            for p in json.loads(DATA_FILE.read_text())["papers"]:
                existing[p["uid"]] = p
        except Exception as exc:
            note(f"warn: could not read existing papers.json: {exc}")

    merged = dict(existing)
    for p in scraped:
        old = merged.get(p["uid"])
        if old and p.get("img") is None and old.get("img"):
            p = {**p, "img": old["img"]}
        merged[p["uid"]] = p

    if not merged:
        note("error: nothing scraped and no existing file")
        return 1

    best = {}
    for p in merged.values():
        key = ((p.get("country") or "").lower(), norm_name(p["name"]))
        cur = best.get(key)
        if cur is None or SRC_PRIORITY.get(p["source"], 9) < SRC_PRIORITY.get(cur["source"], 9):
            best[key] = p
    papers = list(best.values())

    papers.sort(key=lambda p: (
        REGION_ORDER.get(p["region"], 9),
        0 if (p["region"] == "us" and p["state"] is None) else 1,
        (p["state"] or p["country"] or "").lower(),
        p["name"].lower(),
    ))

    now = datetime.now(DENVER) if DENVER else datetime.utcnow() - timedelta(hours=6)
    payload = {
        "generated": now.isoformat(timespec="seconds"),
        "date": now.strftime("%Y-%m-%d"),
        "count": len(papers),
        "papers": papers,
    }
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(payload, indent=1) + "\n")
    print(f"wrote {DATA_FILE}: {len(papers)} papers after dedupe ({len(merged)} before)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
