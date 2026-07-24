#!/usr/bin/env python3
"""
update_papers v5 — refresh data/papers.json from three sources:

  ff  Freedom Forum   (date-stamped image URLs on CloudFront)
      The website blanket rate-limits cloud IPs, so enumeration comes from the
      Internet Archive's CDX index of frontpages.freedomforum.org/newspapers/*
      (URL slugs contain code AND name). Every new candidate code is then
      validated directly against FF's image CDN (not rate-limited) so dead
      codes never enter the list. Direct site fetches are still attempted
      first in case the rate limit lifts.

  fp  FrontPages.com  (per-day hashed image URLs, recorded daily)
      The paper grid on each country page sits between </h1> and the first
      <h2>; nav menus (which contain the literal text "SPORTS/WORLD
      Newspapers") must NOT be used as cut markers. A per-paper og:image
      pass fills any image the listing didn't expose.

  kk  Kiosko.net      (date-stamped image URLs)
      TLS needs OpenSSL security level 0 (legacy signature algorithm).
      Country landing pages cross-link to OTHER countries' geo pages, so each
      paper is attributed by the country code in its own URL, and every geo
      page is fetched at most once globally.

Merging is conservative (a bad scrape day never shrinks the list); dedupe
across sources keeps ff > fp > kk. Diagnostics go to stderr.

No third-party dependencies; stock Python 3.9+.
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
from urllib.parse import unquote, urljoin, urlsplit

try:
    from zoneinfo import ZoneInfo
    DENVER = ZoneInfo("America/Denver")
except Exception:
    DENVER = None

DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "papers.json"
DELAY = 0.25
RETRY_429 = 20
RATE_LIMITED = set()      # hosts that returned 429; don't waste backoffs again

# --- TLS: kiosko.net needs legacy signature algorithms (security level 0) ---
def _make_ctx(seclevel, verify=True):
    ctx = ssl.create_default_context()
    if not verify:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    try:
        ctx.set_ciphers(f"DEFAULT:@SECLEVEL={seclevel}")
    except ssl.SSLError:
        pass
    return ctx

SSL_CTX = _make_ctx(0)
SSL_CTX_LOOSE = _make_ctx(0, verify=False)   # last resort for broken TLS stacks

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
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as r:
            return r.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as e:
        if isinstance(getattr(e, "reason", None), ssl.SSLError):
            with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX_LOOSE) as r:
                return r.read().decode("utf-8", errors="replace")
        raise

def _jina(url):
    txt = _request("https://r.jina.ai/" + url)
    note(f"    jina mirror ok for {url} ({len(txt)} chars)")
    return txt

def get(url, headers=None, mirror=True):
    host = urlsplit(url).netloc
    try:
        txt = _request(url, headers)
        time.sleep(DELAY)
        return txt
    except urllib.error.HTTPError as e:
        note(f"    {url} -> HTTP {e.code}")
        if e.code == 429 and host not in RATE_LIMITED:
            RATE_LIMITED.add(host)
            note(f"    backing off {RETRY_429}s and retrying once…")
            time.sleep(RETRY_429)
            try:
                txt = _request(url, headers)
                time.sleep(DELAY)
                RATE_LIMITED.discard(host)
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

def denver_now():
    return datetime.now(DENVER) if DENVER else datetime.utcnow() - timedelta(hours=6)


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
FF_CDN = "https://d2dr22b2lm4tvw.cloudfront.net"
FF_SLUG = re.compile(r"(?:\\/|/)newspapers(?:\\/|/)([a-z0-9_]+)-([^\"'<>\s\\)?#&]+)")
# CDX index of every archived paper URL (contains code AND name)
FF_CDX = ("https://web.archive.org/cdx/search/cdx"
          "?url=frontpages.freedomforum.org/newspapers/*"
          "&collapse=urlkey&fl=original&limit=8000")

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

def _cdn_alive(code, dates):
    for d in dates:
        url = f"{FF_CDN}/{code}/{d}/front-page-medium.jpg"
        try:
            req = urllib.request.Request(
                url, headers={**BROWSER_HEADERS, "Range": "bytes=0-0"})
            with urllib.request.urlopen(req, timeout=20, context=SSL_CTX):
                return True
        except Exception:
            continue
    return False

def scrape_freedomforum():
    out = {}
    # 1) direct attempts (cheap; work whenever the rate limit isn't biting)
    for url in (FF_BASE + "/", FF_BASE + "/gallery"):
        try:
            _ff_harvest(get(url), out, url)
        except Exception as exc:
            note(f"  ff skip {url}: {exc}")
    try:
        _ff_harvest(_jina(FF_BASE + "/gallery"), out, "gallery [jina-rendered]")
    except Exception as exc:
        note(f"  ff jina gallery: {exc}")
    # 2) Internet Archive CDX enumeration (not rate-limited by FF)
    try:
        _ff_harvest(get(FF_CDX, mirror=False), out, "wayback cdx")
    except Exception as exc:
        note(f"  ff wayback cdx: {exc}")
    # 3) validate NEW candidates against the image CDN so dead codes never land
    known = set()
    try:
        for p in json.loads(DATA_FILE.read_text())["papers"]:
            if p.get("source") == "ff":
                known.add(p["id"])
    except Exception:
        pass
    now = denver_now()
    dates = [now.strftime("%Y-%m-%d"),
             (now - timedelta(days=1)).strftime("%Y-%m-%d")]
    validated, dropped, checked = {}, 0, 0
    for code, p in out.items():
        if code in known:
            validated[code] = p
            continue
        if checked >= 1000:
            break
        checked += 1
        if _cdn_alive(code, dates):
            validated[code] = p
        else:
            dropped += 1
        time.sleep(0.05)
    note(f"  ff validation: {checked} new codes checked against CDN, "
         f"{dropped} dead ones dropped")
    print(f"ff: {len(validated)} papers")
    return list(validated.values())


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
FP_MD_ANCHOR = re.compile(r"\[\*\*(.+?)\*\*.*?\]\(https://www\.frontpages\.com/([a-z0-9\-]+)/\)")
FP_IMG = re.compile(r'([^\s"\'()<>\]]*?/g/(\d{4})/(\d{2})/(\d{2})/([a-z0-9\-]+)-[0-9a-z]+\.[a-z.]+)')
FP_NAME = re.compile(r"<(?:b|strong)[^>]*>(.*?)</(?:b|strong)>", re.S)
FP_OG  = re.compile(r'(?:property|name)="og:image"[^>]*?content="([^"]+)"')
FP_OG2 = re.compile(r'content="([^"]+)"[^>]*?(?:property|name)="og:image"')
FP_SKIP = {"sports-newspapers", "financial-newspapers", "world-newspapers",
           "newspaper-list", "uk-newspapers", "us-newspapers"}

def _fp_main_window(page):
    """The country's own grid sits between </h1> and the first <h2>.
    (Nav menus contain the literal text 'SPORTS/WORLD Newspapers', so text
    markers must not be used.) Markdown mirrors use '# ' / '## ' headings."""
    h1 = page.find("</h1>")
    if h1 != -1:
        start = h1 + len("</h1>")
        h2 = page.find("<h2", start)
        return page[start:h2 if h2 != -1 else len(page)]
    m1 = re.search(r"^# .*$", page, re.M)          # markdown mirror
    if m1:
        start = m1.end()
        m2 = re.search(r"^## ", page[start:], re.M)
        return page[start:start + m2.start()] if m2 else page[start:]
    return page

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
        main = _fp_main_window(page)
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
             f"({len(page)} chars{'' if found else ' — main window: ' + repr(main[:200])})")
    # --- second pass: per-paper og:image for anything missing today's URL
    today_path = denver_now().strftime("%Y/%m/%d")
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
    "ph": "Philippines", "ae": "United Arab Emirates",
}
KK_ANCHOR = re.compile(
    r'<a\b[^>]*href="([^"]*?\bnp/([a-z0-9_\-]+)\.html)"[^>]*>(.*?)</a>', re.S)
KK_MD_ANCHOR = re.compile(
    r"\[([^\]]{3,}?)\]\((?:https?://[a-z0-9.]*kiosko\.net)?/?(?:([a-z]{2})/)?np/([a-z0-9_\-]+)\.html")
KK_TITLE = re.compile(r'title="([^"]*)"')
KK_GEO = re.compile(r'href="([^"]*?\bgeo/[^"]+\.html)"')
KK_HREF_CC = re.compile(r'/([a-z]{2})/np/')

def _kk_clean(name):
    name = re.sub(r"\s*\(.*?\)\s*$", "", name)
    name = re.sub(r"\bnewspaper\b\.?", "", name, flags=re.I).strip(" .-")
    return name

def _kk_add(out, cc, code, name):
    country = KK_COUNTRIES.get(cc)
    if not country:
        return 0
    kid = f"{cc}/{code}"
    if kid in out:
        return 0
    name = _kk_clean(name) or code.replace("_", " ").title()
    state = US_STATE_BY_NAME.get(norm_name(name)) if country == "United States" else None
    out[kid] = dict(uid=f"kk:{kid}", source="kk", id=kid, name=name,
                    country=country, state=state,
                    region=region_for_country(country), img=None)
    return 1

def _kk_harvest(page, page_cc, out):
    n = 0
    for m in KK_ANCHOR.finditer(page):
        href, code, inner = m.group(1), m.group(2), m.group(3)
        mcc = KK_HREF_CC.search(href)
        cc = mcc.group(1) if (mcc and mcc.group(1) in KK_COUNTRIES) else page_cc
        opening = m.group(0).split(">", 1)[0]
        t = KK_TITLE.search(opening)
        alt = re.search(r'alt="([^"]{3,})"', inner)
        name = strip_tags(t.group(1) if t else "") \
            or (strip_tags(alt.group(1)) if alt else "") \
            or strip_tags(inner)
        n += _kk_add(out, cc, code, name)
    for name, mcc, code in KK_MD_ANCHOR.findall(page):     # markdown mirrors
        cc = mcc if mcc in KK_COUNTRIES else page_cc
        n += _kk_add(out, cc, code, strip_tags(name))
    return n

def scrape_kiosko():
    out = {}
    geo_seen = set()
    for cc, country in KK_COUNTRIES.items():
        url = f"{KK_BASE}/{cc}/"
        try:
            page = get(url)
        except Exception as exc:
            note(f"  kk skip {url}: {exc}")
            continue
        n = _kk_harvest(page, cc, out)
        fetched_geos = 0
        for g in KK_GEO.findall(page):
            gu = urljoin(url, g)
            if gu in geo_seen:
                continue
            geo_seen.add(gu)
            # attribute papers on a geo page by the country dir in ITS url
            gm = re.search(r"kiosko\.net/([a-z]{2})/geo/", gu)
            gcc = gm.group(1) if (gm and gm.group(1) in KK_COUNTRIES) else None
            try:
                n += _kk_harvest(get(gu), gcc, out)
                fetched_geos += 1
            except Exception as exc:
                note(f"  kk skip {gu}: {exc}")
        note(f"  kk {url}: running total {len(out)} ({fetched_geos} new geo pages)")
    print(f"kk: {len(out)} papers")
    return list(out.values())


# -------------------------------------------------------------------- main --

REGION_ORDER = {"us": 0, "canada": 1, "mexico": 2, "weurope": 3, "world": 4}
SRC_PRIORITY = {"ff": 0, "fp": 1, "kk": 2}

def main():
    print("update_papers v5")
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

    now = denver_now()
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
