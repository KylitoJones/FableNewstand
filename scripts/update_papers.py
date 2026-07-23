#!/usr/bin/env python3
"""
Refresh data/papers.json from three sources:

  ff  Freedom Forum "Today's Front Pages"  (frontpages.freedomforum.org)
      -> images at d2dr22b2lm4tvw.cloudfront.net/{id}/{YYYY-MM-DD}/front-page-{size}.jpg
         (date-stamped: the site refreshes itself; this script only maintains the list)

  fp  FrontPages.com
      -> images at frontpages.com/g/{Y}/{M}/{D}/{slug}-{hash}.webp.jpg
         (hash is unpredictable, so this script records each day's REAL image URL)

  kk  Kiosko.net
      -> images at img.kiosko.net/{Y}/{M}/{D}/{cc}/{code}.750.jpg
         (date-stamped: self-refreshing; this script only maintains the list)

Entries are merged with the existing papers.json (a bad scrape day never wipes
the list), deduplicated across sources by (country, normalized name) with
priority ff > fp > kk, then bucketed: us -> canada -> mexico -> weurope -> world.

No third-party dependencies; runs on stock Python 3.9+.
"""

import html as htmllib
import json
import re
import sys
import time
import unicodedata
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
    DENVER = ZoneInfo("America/Denver")
except Exception:
    DENVER = None

DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "papers.json"
UA = {"User-Agent": "Mozilla/5.0 (personal front-page reader; GitHub Actions)"}
DELAY = 0.25  # politeness between requests


def fetch(url, timeout=45):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


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

# Freedom Forum code prefixes for non-US papers (not strictly ISO3)
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

# Well-known titles -> US state, for sources that don't encode the state.
# Keyed by normalized name (see norm_name below).
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

FF_SOURCES = [
    "https://frontpages.freedomforum.org/gallery",
    "https://frontpages.freedomforum.org/",
    "https://frontpages.freedomforum.org/sitemap.xml",
    "https://frontpages.freedomforum.org/sitemap-0.xml",
]
FF_SLUG = re.compile(r"/newspapers/([a-z0-9_]+)-([^\"'<>\\\s)?#&]+)")

def scrape_freedomforum():
    from urllib.parse import unquote
    out = {}
    for url in FF_SOURCES:
        try:
            page = fetch(url)
        except Exception as exc:
            print(f"warn ff: {url}: {exc}", file=sys.stderr); continue
        for code, raw in FF_SLUG.findall(page):
            name = re.sub(r"\s+", " ", unquote(raw).replace("_", " ")).strip()
            if not name or code in out:
                continue
            prefix = code.split("_", 1)[0] if "_" in code else code
            if "_" in code and prefix in US_STATES:
                country, state = "United States", US_STATES[prefix]
            elif prefix in FF_COUNTRIES:
                country, state = FF_COUNTRIES[prefix], None
            elif "_" not in code:
                country, state = "United States", None   # national titles: wsj, usat...
            else:
                country, state = prefix.upper(), None
            out[code] = dict(uid=f"ff:{code}", source="ff", id=code, name=name,
                             country=country, state=state,
                             region=region_for_country(country) if country != prefix.upper()
                                    else "world", img=None)
        time.sleep(DELAY)
    print(f"ff: {len(out)} papers")
    return list(out.values())


# ------------------------------------------------------- source: frontpages.com

FP_BASE = "https://www.frontpages.com"
# per-country listing pages; 'uk' aggregate is used instead of its constituents
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
FP_IMG = re.compile(r'/g/(\d{4})/(\d{2})/(\d{2})/([a-z0-9\-]+)-[0-9a-z]+\.[a-z.]+')
FP_NAME = re.compile(r"<(?:b|strong)[^>]*>(.*?)</(?:b|strong)>", re.S)
FP_SKIP = {"sports-newspapers", "financial-newspapers", "world-newspapers",
           "newspaper-list", "uk-newspapers", "us-newspapers"}

def scrape_frontpages():
    out = {}
    for cslug, country in FP_COUNTRIES.items():
        url = f"{FP_BASE}/{cslug}-newspapers/"
        try:
            page = fetch(url)
        except Exception as exc:
            print(f"warn fp: {url}: {exc}", file=sys.stderr); continue
        main = page.split("<h2")[0]          # only the country's own grid
        # today's real image URL per slug (hash is unpredictable)
        imgs = {}
        for y, m, d, slug in FP_IMG.findall(main):
            imgs.setdefault(slug, None)
            full = re.search(r'["\'(]([^"\'()\s]*?/g/' + y + "/" + m + "/" + d +
                             "/" + re.escape(slug) + r'-[0-9a-z]+\.[a-z.]+)', main)
            if full:
                u = full.group(1)
                if u.startswith("/"): u = FP_BASE + u
                imgs[slug] = u
        for slug, inner in FP_ANCHOR.findall(main):
            if slug in FP_SKIP or slug.endswith("-newspapers") or slug.endswith("-sports") \
               or slug in out:
                continue
            nm = FP_NAME.search(inner)
            name = strip_tags(nm.group(1) if nm else inner)
            name = re.sub(r"\s*(Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)day,.*$", "", name).strip()
            if not name:
                continue
            state = US_STATE_BY_NAME.get(norm_name(name)) if country == "United States" else None
            out[slug] = dict(uid=f"fp:{slug}", source="fp", id=slug, name=name,
                             country=country, state=state,
                             region=region_for_country(country),
                             img=imgs.get(slug))
        time.sleep(DELAY)
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

def scrape_kiosko():
    out = {}
    for cc, country in KK_COUNTRIES.items():
        url = f"{KK_BASE}/{cc}/"
        try:
            page = fetch(url)
        except Exception as exc:
            print(f"warn kk: {url}: {exc}", file=sys.stderr); continue
        for m in KK_ANCHOR.finditer(page):
            code, inner = m.group(1), m.group(2)
            kid = f"{cc}/{code}"
            if kid in out:
                continue
            opening = m.group(0).split(">", 1)[0]
            t = KK_TITLE.search(opening)
            # fallback: alt text of an inner thumbnail
            alt = re.search(r'alt="([^"]{3,})"', inner)
            name = strip_tags(t.group(1) if t else "") \
                or (strip_tags(alt.group(1)) if alt else "") \
                or strip_tags(inner)
            name = re.sub(r"\s*\(.*?\)\s*$", "", name)          # drop "(Canada)"
            name = re.sub(r"\bnewspaper\b\.?", "", name, flags=re.I).strip(" .-")
            if not name:
                name = code.replace("_", " ").title()
            state = US_STATE_BY_NAME.get(norm_name(name)) if country == "United States" else None
            out[kid] = dict(uid=f"kk:{kid}", source="kk", id=kid, name=name,
                            country=country, state=state,
                            region=region_for_country(country), img=None)
        time.sleep(DELAY)
    print(f"kk: {len(out)} papers")
    return list(out.values())


# -------------------------------------------------------------------- main --

REGION_ORDER = {"us": 0, "canada": 1, "mexico": 2, "weurope": 3, "world": 4}
SRC_PRIORITY = {"ff": 0, "fp": 1, "kk": 2}

def main():
    scraped = scrape_freedomforum() + scrape_frontpages() + scrape_kiosko()

    # merge with existing file: entries persist; fresh data wins,
    # but never overwrite a known fp image URL with nothing.
    existing = {}
    if DATA_FILE.exists():
        try:
            for p in json.loads(DATA_FILE.read_text())["papers"]:
                existing[p["uid"]] = p
        except Exception as exc:
            print(f"warn: could not read existing papers.json: {exc}", file=sys.stderr)

    merged = dict(existing)
    for p in scraped:
        old = merged.get(p["uid"])
        if old and p.get("img") is None and old.get("img"):
            p = {**p, "img": old["img"]}
        merged[p["uid"]] = p

    if not merged:
        print("error: nothing scraped and no existing file", file=sys.stderr)
        return 1

    # cross-source dedupe by (country, normalized name), ff > fp > kk
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
    print(f"wrote {DATA_FILE}: {len(papers)} papers after dedupe "
          f"({len(merged)} before)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
