#!/usr/bin/env python3
"""
Cambridge Politics & Law events scraper.

Pulls upcoming events from:
  * talks.cam.ac.uk  (keyword search -> series XML feeds, plus curated series)
  * Department sites  (POLIS, Faculty of Law incl. LCIL & law centres,
                       Institute of Criminology, Bennett School of Public Policy)
  * Eventbrite        (Cambridge politics / law / government listings)

Writes events.json and events.js (the latter lets index.html work when
opened straight from disk). Standard library only - no pip installs needed.
"""
import concurrent.futures as cf
import datetime as dt
import html
import json
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
    LONDON = ZoneInfo("Europe/London")
except Exception:  # pragma: no cover
    LONDON = dt.timezone.utc

HERE = Path(__file__).resolve().parent
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X) CamPolLawCalendar/1.0 (personal event planner)"
DAYS_AHEAD = 120
TODAY = dt.datetime.now(LONDON).date()
HORIZON = TODAY + dt.timedelta(days=DAYS_AHEAD)

# --------------------------------------------------------------------------
# Topic tagging
# --------------------------------------------------------------------------
TOPICS = {
    "Law": r"\blaw\b|\blaws\b|legal|jurisprud|court|judge|judicia|litigat|lawyer|barrister|solicitor|"
           r"arbitrat|tribunal|statut|constitution|contract law|\btort|lcil|lauterpacht|barristers?\b|pupillage|"
           r"\bcriminal law|legislat|faculty of law|intellectual property|copyright|moot",
    "Politics": r"politic|polis\b|democra|election|elector|parliament|political part|populis|government|"
                r"authoritarian|sovereign|citizenship|nationalis|liberalism|conservative|anarch|"
                r"protest|revolution|union society|minister|monarch|oligarch|domination|republic",
    "International": r"international|global|geopolit|diploma|foreign|\bwar\b|wars\b|conflict|security|"
                     r"united nations|\beu\b|european union|nato|china|russia|ukraine|taiwan|gaza|israel|"
                     r"palestin|middle east|baltic|belarus|empire|imperial|colonial|migration|refugee",
    "Policy": r"public policy|\bpolicy\b|policies|public sector|bennett|governance|\bai act|welfare state|"
              r"\bfiscal|semiconductor strategy|digital economics|climate (crisis|change|law)",
    "Rights & Justice": r"human rights|\brights\b|justice|equality|discriminat|genocide|"
                        r"atrocit|humanitarian|feminis|racial|disability rights|reproductive",
    "Criminology": r"criminolog|\bcrimes?\b|criminal|prison|policing|\bpolice\b|punish|sentenc",
    "Political Thought": r"political thought|political theory|philosoph|intellectual history|hobbes|"
                         r"rousseau|marx|arendt|rawls|ideolog|secularism|political philosophy",
    "Entrepreneurship": r"entrepreneur|start-?ups?\b|founders?\b|venture capital|venture creation|spin-?outs?\b|"
                        r"accelerate cambridge|accelerator|\bpitch(ing)?\b|investors?\b|\bvc\b|commerciali[sz]|"
                        r"scale-?ups?\b|incubat|business model|e-?lab\b|enterprise tuesday|social innovation",
    "Effective Altruism": r"effective altruis|\bEA (cambridge|fellowship|social|talk)|existential risk|x-risk|"
                          r"ai safety|longtermis|animal welfare|80,?000 hours|giving what we can",
}
TOPIC_RX = {k: re.compile(v, re.I) for k, v in TOPICS.items()}
# A stricter test for "is this genuinely about politics or law" (for broad sources).
CORE_RX = re.compile(TOPICS["Law"] + "|" + TOPICS["Politics"] + "|" + TOPICS["International"] + "|"
                     + TOPICS["Policy"] + "|" + TOPICS["Criminology"] + "|" + TOPICS["Entrepreneurship"] + "|"
                     + TOPICS["Effective Altruism"] + r"|human rights|justice", re.I)
ENT_RX = re.compile(TOPICS["Entrepreneurship"] + "|" + TOPICS["Effective Altruism"], re.I)


LAWPOL_RX = re.compile(TOPICS["Law"] + "|" + TOPICS["Politics"] + "|" + TOPICS["Entrepreneurship"]
                       + r"|international (law|relations|affairs)|human rights|effective altruis", re.I)


def tag(*texts):
    blob = " ".join(t for t in texts if t)
    return [k for k, rx in TOPIC_RX.items() if rx.search(blob)]


# --------------------------------------------------------------------------
# HTTP helpers
# --------------------------------------------------------------------------
_ctx = ssl.create_default_context()


def fetch(url, timeout=25, tries=2):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en-GB,en"})
            with urllib.request.urlopen(req, timeout=timeout, context=_ctx) as r:
                return r.read().decode("utf-8", errors="replace")
        except Exception as e:  # noqa
            last = e
            time.sleep(0.8 * (i + 1))
    raise last


def strip_tags(s):
    s = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", s or "", flags=re.S | re.I)
    s = re.sub(r"<br\s*/?>|</p>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t\r\f\v]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n\n", s)
    return s.strip()


def to_local(d):
    """Aware/naive datetime -> naive Europe/London ISO string (minute precision)."""
    if d is None:
        return None
    if d.tzinfo is not None:
        d = d.astimezone(LONDON).replace(tzinfo=None)
    return d.strftime("%Y-%m-%dT%H:%M")


def in_window(start_iso):
    try:
        d = dt.date.fromisoformat(start_iso[:10])
    except Exception:
        return False
    return TODAY - dt.timedelta(days=1) <= d <= HORIZON


def make_event(**kw):
    ev = {
        "id": kw.get("id"),
        "title": (kw.get("title") or "").strip(),
        "start": kw.get("start"),
        "end": kw.get("end"),
        "allDay": bool(kw.get("allDay")),
        "venue": (kw.get("venue") or "").strip(),
        "speaker": (kw.get("speaker") or "").strip(),
        "organiser": (kw.get("organiser") or "").strip(),
        "series": (kw.get("series") or "").strip(),
        "description": (kw.get("description") or "").strip()[:1500],
        "url": kw.get("url"),
        "source": kw.get("source"),
        "sourceGroup": kw.get("sourceGroup"),
        "booking": kw.get("booking"),
    }
    ev["topics"] = kw.get("topics") or tag(ev["title"], ev["series"], ev["description"][:300])
    return ev


# --------------------------------------------------------------------------
# talks.cam.ac.uk
# --------------------------------------------------------------------------
TALKS = "https://talks.cam.ac.uk"
TALKS_KEYWORDS = [
    "politics", "political", "law", "legal", "international law", "international relations",
    "democracy", "constitution", "constitutional", "human rights", "government", "public policy",
    "geopolitics", "criminology", "justice", "election", "sovereignty", "diplomacy", "war",
    "European Union", "parliament", "populism", "authoritarian", "governance", "court", "rights",
    "Lauterpacht", "POLIS", "Bennett", "regulation", "security", "empire", "nationalism",
    # societies
    "Politics Society", "Labour Club", "Conservative Association", "Liberal Association", "Cambridge Union",
    "Law Society", "Geopolitics", "CGHR", "effective altruism", "AI safety",
    # entrepreneurship
    "entrepreneurship", "entrepreneur", "startup", "start-up", "venture capital", "Enterprise Tuesday",
    "innovation", "spin-out", "founder", "Accelerate Cambridge",
]
# Series known to be politics/law relevant (always fetched).
TALKS_SEED_LISTS = [22703, 64911, 130150]
SERIES_RELEVANT_RX = re.compile(
    r"politic|polis|\blaw\b|legal|lauterpacht|international|criminolog|human rights|public policy|"
    r"bennett|democra|constitution|justice|centre for public law|cghr|geopolit|crassh|"
    r"history and politics|political thought|government|diplomac|international (affairs|studies|relations)|"
    r"entrepreneur|enterprise tuesday|venture|start-?up|effective altruism|politics society|labour club|"
    r"conservative association|liberal association|cambridge union|law society",
    re.I)
TITLE_CORE_RX = re.compile(
    r"politic|\blaw\b|\blaws\b|legal|democra|constitution|human rights|government|public policy|\bpolicy\b|"
    r"geopolit|criminolog|justice|election|sovereign|diploma|parliament|populis|authoritarian|"
    r"international (law|relations|order|affairs)|\bwar\b|nationalis|\bstate\b|empire|colonial|"
    r"\brights\b|court|protest|citizen|refugee|migration|genocide|ukraine|russia|china|gaza|palestin|israel|"
    + TOPICS["Entrepreneurship"] + "|" + TOPICS["Effective Altruism"], re.I)


def talks_search(keyword):
    url = f"{TALKS}/search?search={urllib.parse.quote(keyword)}"
    h = fetch(url)
    out = []
    for m in re.finditer(r'<a href="/talk/index/(\d+)/">(.*?)</a>.*?<span class="time">([^<]+)</span>', h, re.S):
        tid, title, when = m.group(1), m.group(2), m.group(3).strip()
        try:
            d = dt.datetime.strptime(when, "%a %d %b %Y, %H:%M").date()
        except ValueError:
            continue
        if TODAY - dt.timedelta(days=1) <= d <= HORIZON:
            out.append(int(tid))
    return out


def talk_series_id(tid):
    h = fetch(f"{TALKS}/talk/index/{tid}/")
    m = re.search(r'id="bread".*?<a href="/show/index/(\d+)/">', h, re.S)
    return int(m.group(1)) if m else None


def talks_list_xml(list_id):
    url = (f"{TALKS}/show/xml/{list_id}?seconds_before_today=86400"
           f"&seconds_after_today={DAYS_AHEAD * 86400}")
    root = ET.fromstring(fetch(url).encode("utf-8"))
    name = (root.findtext("name") or "").strip()
    talks = []
    for t in root.findall("talk"):
        def g(k):
            return (t.findtext(k) or "").strip()
        try:
            s = dt.datetime.strptime(g("start_time"), "%a, %d %b %Y %H:%M:%S %z")
            e = dt.datetime.strptime(g("end_time"), "%a, %d %b %Y %H:%M:%S %z") if g("end_time") else None
        except ValueError:
            continue
        talks.append(make_event(
            id=f"talks-{g('id')}", title=html.unescape(g("title")), start=to_local(s), end=to_local(e),
            venue=g("venue"), speaker=g("speaker"), organiser=g("organiser"),
            series=g("series") or name, description=strip_tags(html.unescape(g("abstract"))),
            url=g("url"), source=f"talks.cam: {g('series') or name}", sourceGroup="talks.cam",
        ))
    return name, talks


def scrape_talks(log):
    found_ids = set()
    with cf.ThreadPoolExecutor(6) as ex:
        for ids in ex.map(lambda k: _safe(talks_search, k, log, []), TALKS_KEYWORDS):
            found_ids.update(ids)
    log(f"talks.cam: {len(found_ids)} upcoming talks matched keyword searches")

    with cf.ThreadPoolExecutor(8) as ex:
        series = dict(zip(found_ids, ex.map(lambda t: _safe(talk_series_id, t, log, None), found_ids)))
    list_ids = {s for s in series.values() if s} | set(TALKS_SEED_LISTS)

    events = {}
    with cf.ThreadPoolExecutor(8) as ex:
        results = dict(zip(list_ids, ex.map(lambda l: _safe(talks_list_xml, l, log, ("", [])), list_ids)))
    for lid, (name, talks) in results.items():
        series_relevant = bool(SERIES_RELEVANT_RX.search(name)) or lid in TALKS_SEED_LISTS
        for ev in talks:
            tid = int(ev["id"].split("-")[1])
            keep = series_relevant or (tid in found_ids and TITLE_CORE_RX.search(ev["title"]))
            if keep and in_window(ev["start"]):
                events[ev["id"]] = ev
    log(f"talks.cam: {len(events)} events from {len(list_ids)} series")
    return list(events.values())


# --------------------------------------------------------------------------
# University Drupal sites (shared "cambridge_tailwind" card layout)
# --------------------------------------------------------------------------
DEPT_SITES = [
    ("POLIS (Politics & International Studies)", "https://www.polis.cam.ac.uk", "/events"),
    ("Faculty of Law", "https://www.law.cam.ac.uk", "/events"),
    ("Institute of Criminology", "https://www.crim.cam.ac.uk", "/events"),
]
MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"], 1)}
DATE_RX = re.compile(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})")
TIME_RX = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)", re.I)


def _parse_time(m):
    h, mi, ap = int(m.group(1)), int(m.group(2) or 0), m.group(3).lower()
    if ap == "pm" and h != 12:
        h += 12
    if ap == "am" and h == 12:
        h = 0
    return h, mi


def parse_card_when(text):
    """'19 November 2026 * 5:00pm to 7:00pm' / '3 March 2027 to 4 March 2027' -> (start,end,allDay)."""
    dates = DATE_RX.findall(text)
    if not dates:
        return None, None, False
    def mkdate(t):
        return dt.date(int(t[2]), MONTHS[t[1].lower()], int(t[0]))
    try:
        d1 = mkdate(dates[0])
        d2 = mkdate(dates[1]) if len(dates) > 1 else d1
    except (KeyError, ValueError):
        return None, None, False
    times = list(TIME_RX.finditer(text))
    if times:
        h, mi = _parse_time(times[0])
        s = dt.datetime.combine(d1, dt.time(h, mi))
        e = None
        if len(times) > 1:
            h2, m2 = _parse_time(times[1])
            e = dt.datetime.combine(d2, dt.time(h2, m2))
        # Some site entries are mis-typed as am (e.g. a 5:00am talk); assume pm for 1-7am.
        if s.hour < 8 and (e is None or e.hour < 9):
            s += dt.timedelta(hours=12)
            e = e + dt.timedelta(hours=12) if e else None
        return to_local(s), to_local(e) if e else None, False
    return f"{d1.isoformat()}T00:00", f"{d2.isoformat()}T23:59", True


def scrape_drupal(name, base, path, log, max_pages=6):
    events, seen = [], set()
    for page in range(max_pages):
        url = f"{base}{path}" + (f"?page={page}" if page else "")
        try:
            h = fetch(url)
        except Exception as e:
            log(f"{name}: page {page} failed ({e})")
            break
        cards = re.findall(
            r'<a href="([^"]+)" class="card-link[^"]*".*?card-heading">(.*?)</p>.*?card-subheading">(.*?)</p>',
            h, re.S)
        new = 0
        for href, title, sub in cards:
            if href in seen or "/events/" not in href:
                continue
            seen.add(href)
            new += 1
            sub_txt = strip_tags(sub)
            start, end, all_day = parse_card_when(sub_txt)
            if not start or not in_window(start):
                continue
            # venue = whatever follows the times
            last = list(re.finditer(r"\d(?::\d\d)?\s*(?:am|pm)|\b\d{4}\b", sub_txt, re.I))
            venue = sub_txt[last[-1].end():] if last else sub_txt
            venue = re.sub(r"\s+", " ", venue).strip(" *·•-|")
            full = urllib.parse.urljoin(base, href)
            events.append(make_event(
                id="dept-" + re.sub(r"\W+", "-", full.split("//")[1]), title=strip_tags(title),
                start=start, end=end, allDay=all_day, venue=venue, organiser=name, url=full,
                source=name, sourceGroup="Department",
            ))
        if not new or f"page={page + 1}" not in h:
            break
    # Enrich with a short description from detail pages (in parallel).
    def detail(ev):
        try:
            d = fetch(ev["url"])
            m = re.search(r"<main.*?</main>", d, re.S)
            txt = strip_tags(m.group(0) if m else d)
            i = txt.find(ev["title"][:40])
            ev["description"] = txt[i + len(ev["title"]): i + len(ev["title"]) + 1200].strip() if i >= 0 else txt[:1000]
            if not ev["topics"]:
                ev["topics"] = tag(ev["title"], ev["description"][:600])
        except Exception:
            pass
        return ev
    with cf.ThreadPoolExecutor(8) as ex:
        events = list(ex.map(detail, events))
    for ev in events:
        # Department pages are inherently on-topic; give them a home tag.
        home = "Law" if "Law" in name else "Criminology" if "Crim" in name else "Politics"
        if home not in ev["topics"]:
            ev["topics"].insert(0, home)
    log(f"{name}: {len(events)} upcoming events")
    return events


# --------------------------------------------------------------------------
# Bennett School of Public Policy (WordPress)
# --------------------------------------------------------------------------
def scrape_bennett(log):
    base = "https://www.bennettschool.cam.ac.uk/events/"
    h = fetch(base)
    links = sorted(set(re.findall(r'https://www\.bennettschool\.cam\.ac\.uk/events/[a-z0-9-]+/', h)))
    links = [l for l in links if not l.endswith("/feed/")]

    def one(url):
        try:
            p = fetch(url)
        except Exception:
            return None
        txt = strip_tags(re.sub(r"<(header|footer|nav)[^>]*>.*?</\1>", " ", p, flags=re.S))
        m = re.search(r"Event details\s+(\d{1,2})/(\d{1,2})/(\d{4})(.{0,80})", txt, re.S)
        if not m:
            return None
        d = dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        tail = m.group(4)
        tm = list(re.finditer(r"(\d{1,2})[:.](\d{2})", tail))
        title_m = re.search(r"<h1[^>]*>(.*?)</h1>", p, re.S)
        title = strip_tags(title_m.group(1)) if title_m else url.rstrip("/").split("/")[-1]
        if tm:
            s = dt.datetime.combine(d, dt.time(int(tm[0].group(1)), int(tm[0].group(2))))
            e = dt.datetime.combine(d, dt.time(int(tm[1].group(1)), int(tm[1].group(2)))) if len(tm) > 1 else None
            start, end, all_day = to_local(s), to_local(e), False
        else:
            start, end, all_day = f"{d}T00:00", f"{d}T23:59", True
        venue = re.sub(r"^\s*\d{1,2}[:.]\d{2}.*?\d{1,2}[:.]\d{2}\s*", "", tail).split(" Speakers")[0].strip()
        desc_m = re.search(r'<meta name="description" content="([^"]*)"', p) or \
            re.search(r'<meta property="og:description" content="([^"]*)"', p)
        return make_event(
            id="bennett-" + url.rstrip("/").split("/")[-1], title=title, start=start, end=end, allDay=all_day,
            venue=re.sub(r"\s+", " ", re.sub(r"^[\s|]*\d{1,2}[:.]\d{2}\s*-\s*\d{1,2}[:.]\d{2}\s*(\([^)]*\))?", "", venue)).strip(" |")[:120], organiser="Bennett School of Public Policy", url=url,
            description=html.unescape(desc_m.group(1)) if desc_m else "",
            source="Bennett School of Public Policy", sourceGroup="Department",
        )
    with cf.ThreadPoolExecutor(8) as ex:
        evs = [e for e in ex.map(one, links) if e and in_window(e["start"])]
    for e in evs:
        if "Policy" not in e["topics"]:
            e["topics"].insert(0, "Policy")
    log(f"Bennett School: {len(evs)} upcoming events")
    return evs


# --------------------------------------------------------------------------
# Eventbrite
# --------------------------------------------------------------------------
EVENTBRITE_PAGES = [
    "https://www.eventbrite.co.uk/d/united-kingdom--cambridge/politics/",
    "https://www.eventbrite.co.uk/d/united-kingdom--cambridge/law/",
    "https://www.eventbrite.co.uk/d/united-kingdom--cambridge/government/",
    "https://www.eventbrite.co.uk/d/united-kingdom--cambridge/international-relations/",
    "https://www.eventbrite.co.uk/d/united-kingdom--cambridge/public-policy/",
    "https://www.eventbrite.co.uk/d/united-kingdom--cambridge/human-rights/",
    "https://www.eventbrite.co.uk/d/united-kingdom--cambridge/entrepreneurship/",
    "https://www.eventbrite.co.uk/d/united-kingdom--cambridge/startups/",
    "https://www.eventbrite.co.uk/d/united-kingdom--cambridge/venture-capital/",
]
CAM_ORG_RX = re.compile(r"cambridge|polis|politics|law|bennett|crassh|lauterpacht|college", re.I)


def _ld_json(h):
    for m in re.findall(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', h, re.S):
        try:
            yield json.loads(m)
        except Exception:
            continue


def scrape_eventbrite(log):
    urls = {}
    for page_url in EVENTBRITE_PAGES:
        for pg in (1, 2):
            try:
                h = fetch(page_url + (f"?page={pg}" if pg > 1 else ""))
            except Exception as e:
                log(f"Eventbrite {page_url} p{pg}: {e}")
                break
            for d in _ld_json(h):
                if isinstance(d, dict) and d.get("@type") == "ItemList":
                    for it in d.get("itemListElement", []):
                        e = it.get("item", {})
                        loc = (e.get("location") or {}).get("address", {}) or {}
                        if "cambridge" not in str(loc.get("addressLocality", "")).lower():
                            continue
                        if not in_window(e.get("startDate", "")):
                            continue
                        u = e.get("url", "").split("?")[0]
                        if u:
                            urls[u] = e
    log(f"Eventbrite: {len(urls)} Cambridge listings found; fetching details")

    def one(u):
        base = urls[u]
        start = end = None
        venue, organiser, desc = "", "", base.get("description", "")
        try:
            for d in _ld_json(fetch(u)):
                if isinstance(d, dict) and "Event" in str(d.get("@type", "")):
                    s = dt.datetime.fromisoformat(d["startDate"].replace("Z", "+00:00"))
                    start = to_local(s)
                    if d.get("endDate"):
                        end = to_local(dt.datetime.fromisoformat(d["endDate"].replace("Z", "+00:00")))
                    loc = d.get("location") or {}
                    loc = loc if isinstance(loc, dict) else {}
                    addr = loc.get("address") or {}
                    addr = addr if isinstance(addr, dict) else {}
                    venue = ", ".join(x for x in [loc.get("name"), addr.get("streetAddress")] if x)
                    org = d.get("organizer") or {}
                    if isinstance(org, list):
                        org = org[0] if org else {}
                    organiser = (org.get("name") if isinstance(org, dict) else "") or ""
                    desc = d.get("description") or desc
                    break
        except Exception:
            pass
        if not start:
            start, end = base["startDate"][:10] + "T00:00", base["startDate"][:10] + "T23:59"
        ev = make_event(
            id="eb-" + re.sub(r"\D", "", u)[-13:], title=html.unescape(base.get("name", "")), start=start, end=end,
            allDay=start.endswith("T00:00") and (end or "").endswith("T23:59"), venue=venue,
            organiser=organiser, description=strip_tags(desc), url=u, booking=u,
            source=f"Eventbrite: {organiser}" if organiser else "Eventbrite", sourceGroup="Eventbrite",
        )
        relevant = TITLE_CORE_RX.search(ev["title"]) or LAWPOL_RX.search(ev["description"][:400]) or (
            re.search(r"polis|politic|faculty of law|law faculty|bennett|crassh|lauterpacht", organiser, re.I))
        return ev if relevant else None

    def safe_one(u):
        try:
            return one(u)
        except Exception as e:
            log(f"  ! Eventbrite {u}: {e}")
            return None
    with cf.ThreadPoolExecutor(8) as ex:
        evs = [e for e in ex.map(safe_one, list(urls)) if e]
    log(f"Eventbrite: {len(evs)} relevant events")
    return evs



# --------------------------------------------------------------------------
# Generic date helpers for free-text event pages
# --------------------------------------------------------------------------
LONG_DATE_RX = re.compile(
    r"(\d{1,2})(?:st|nd|rd|th)?\s+(January|February|March|April|May|June|July|August|September|October|"
    r"November|December),?\s+(\d{4})", re.I)
NUM_DATE_RX = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
HHMM_RX = re.compile(r"\b(\d{1,2})[:.](\d{2})\s*(am|pm)?", re.I)


def find_when(text):
    """Find the first date in free text plus a time within ~40 chars of it."""
    m = LONG_DATE_RX.search(text)
    if m:
        d = dt.date(int(m.group(3)), MONTHS[m.group(2).lower()], int(m.group(1)))
    else:
        m = NUM_DATE_RX.search(text)
        if not m:
            return None, None, False, None
        d = dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    window = text[max(0, m.start() - 40): m.end() + 60]
    times = []
    for t in HHMM_RX.finditer(window):
        h, mi = int(t.group(1)), int(t.group(2))
        if t.group(3):
            ap = t.group(3).lower()
            h = h + 12 if ap == "pm" and h != 12 else (0 if ap == "am" and h == 12 else h)
        if h < 24 and mi < 60:
            times.append((h, mi))
    if times:
        s = dt.datetime.combine(d, dt.time(*times[0]))
        e = dt.datetime.combine(d, dt.time(*times[1])) if len(times) > 1 and times[1] > times[0] else None
        return to_local(s), to_local(e), False, m
    return f"{d}T00:00", f"{d}T23:59", True, m


def _meta(h, prop):
    m = re.search(r'<meta (?:property|name)="%s" content="([^"]*)"' % re.escape(prop), h)
    return html.unescape(m.group(1)) if m else ""


def _h1(h):
    m = re.search(r"<h1[^>]*>(.*?)</h1>", h, re.S)
    return strip_tags(m.group(1)) if m else _meta(h, "og:title")


# --------------------------------------------------------------------------
# Research centres on WordPress: Centre for Geopolitics, CGHR
# --------------------------------------------------------------------------
WP_CENTRES = [
    # name, listing url, link regex, home topic
    ("Centre for Geopolitics", "https://www.cfg.cam.ac.uk/events/",
     r'https://www\.cfg\.cam\.ac\.uk/events/[a-z0-9-]+/', "International"),
    ("Centre of Governance and Human Rights (CGHR)", "https://www.cghr.polis.cam.ac.uk/events/",
     r'https://www\.cghr\.polis\.cam\.ac\.uk/events/[a-z0-9-]+/', "Rights & Justice"),
]


def scrape_wp_centre(name, url, link_rx, home, log):
    h = fetch(url)
    links = [l for l in dict.fromkeys(re.findall(link_rx, h)) if "/feed/" not in l and "/page/" not in l]

    def one(link):
        try:
            p = fetch(link)
        except Exception:
            return None
        main = re.search(r"<main.*?</main>", p, re.S)
        txt = strip_tags(main.group(0) if main else p).replace("\n", " ")
        title = _h1(p)
        start, end, all_day, m = find_when(txt)
        if not start or not in_window(start):
            return None
        venue = ""
        v = re.search(r"In-person:\s*(.{5,120}?)(?:Zoom|Online|To attend|\.\s)", txt)
        if v:
            venue = v.group(1)
        elif m:
            after = txt[m.end():m.end() + 200]
            after = re.sub(r"^\s*\d{1,2}[:.]\d{2}(\s*[-–]\s*\d{1,2}[:.]\d{2})?\s*", "", after)
            venue = re.split(r"Research themes|Share|Register|Book|Event\b", after)[0]
        venue = re.sub(r"\s+", " ", venue).strip(" ,|-")
        if len(venue) > 90 or not re.search(r"room|hall|theatre|building|college|centre|center|online|zoom|"
                                            r"library|seminar|lecture|institute|school|house|cambridge", venue, re.I):
            venue = ""
        desc = _meta(p, "og:description") or _meta(p, "description")
        return make_event(
            id="centre-" + link.rstrip("/").split("/")[-1], title=title, start=start, end=end, allDay=all_day,
            venue=re.sub(r"\s+", " ", venue).strip(" ,|-")[:140], organiser=name, url=link, description=desc,
            source=name, sourceGroup="Research centre",
        )
    with cf.ThreadPoolExecutor(8) as ex:
        evs = [e for e in ex.map(one, links[:40]) if e]
    for e in evs:
        if home not in e["topics"]:
            e["topics"].insert(0, home)
    log(f"{name}: {len(evs)} upcoming events")
    return evs


# --------------------------------------------------------------------------
# Entrepreneurship: King's E-Lab, Judge Business School, Cambridge Enterprise
# --------------------------------------------------------------------------
def _home(evs, topic):
    for e in evs:
        if topic not in e["topics"]:
            e["topics"].insert(0, topic)
    return evs


def scrape_kings_elab(log):
    d = json.loads(fetch("https://www.kingselab.org/all-events?format=json"))
    evs = []
    for it in d.get("upcoming", []):
        s = dt.datetime.fromtimestamp(it["startDate"] / 1000, LONDON)
        e = dt.datetime.fromtimestamp(it["endDate"] / 1000, LONDON) if it.get("endDate") else None
        loc = it.get("location") or {}
        venue = ", ".join(html.unescape(x) for x in [loc.get("addressTitle") or "", loc.get("addressLine1") or ""] if x)
        url = "https://www.kingselab.org" + it.get("fullUrl", "")
        evs.append(make_event(
            id="elab-" + it.get("id", url), title=html.unescape(it.get("title", "")), start=to_local(s), end=to_local(e),
            venue=venue, organiser="King's Entrepreneurship Lab (King's E-Lab)", url=url,
            description=strip_tags(it.get("excerpt") or it.get("body") or ""),
            source="King's E-Lab", sourceGroup="Entrepreneurship",
        ))
    evs = [e for e in evs if in_window(e["start"])]
    log(f"King's E-Lab: {len(evs)} upcoming events")
    return _home(evs, "Entrepreneurship")


JBS_KEEP_RX = re.compile(r"entrepreneur|accelerate|venture|innovation|enterprise|economics and policy|policy|"
                         r"el-erian|digital innovation|artificial intelligence|social innovation|conference", re.I)
JBS_DROP_RX = re.compile(r"admission", re.I)


def scrape_jbs(log):
    base = "https://www.jbs.cam.ac.uk/wp-json/cjbs-event/v1/search?view=list&pagination="
    first = json.loads(fetch(base + "1"))
    pages = [first] + [json.loads(fetch(base + str(i))) for i in range(2, min(int(first.get("pages", 1)), 10) + 1)]
    evs = []
    for pg in pages:
        for it in pg.get("post", []):
            cats = strip_tags(it.get("categoryField", "")).replace("Category:", "")
            locs = " ".join(it.get("location") or [])
            if JBS_DROP_RX.search(cats + " " + it.get("title", "")) or re.search(r"meet the director", it.get("title", ""), re.I):
                continue
            if not (JBS_KEEP_RX.search(cats) or ENT_RX.search(it.get("title", ""))):
                continue
            if locs and not re.search(r"cambridge|online", locs, re.I):
                continue
            s = dt.datetime.fromtimestamp(int(it["start_date"]), LONDON)
            end = None
            tm = re.match(r"(\d{2}):(\d{2})-(\d{2}):(\d{2})", it.get("time") or "")
            if tm:
                end = s.replace(hour=int(tm.group(3)), minute=int(tm.group(4)))
                if end <= s:
                    end = None
            evs.append(make_event(
                id="jbs-" + it["permalink"].rstrip("/").split("/")[-1], title=html.unescape(it["title"]),
                start=to_local(s), end=to_local(end), allDay=bool(it.get("tbc")),
                venue="Online" if "online" in locs.lower() else "Cambridge Judge Business School",
                organiser="Cambridge Judge Business School", series=cats.strip(), url=it["permalink"],
                description=html.unescape(it.get("excerpt") or ""), source="Cambridge Judge Business School",
                sourceGroup="Entrepreneurship",
            ))
    evs = [e for e in evs if in_window(e["start"])]
    for e in evs:
        if re.search(r"entrepreneur|accelerate|venture|enterprise", e["series"] + " " + e["title"], re.I):
            _home([e], "Entrepreneurship")
        elif "Policy" not in e["topics"]:
            e["topics"].insert(0, "Policy")
    log(f"Judge Business School: {len(evs)} entrepreneurship/policy events")
    return evs


def scrape_cambridge_enterprise(log):
    url = "https://www.enterprise.cam.ac.uk/our-events/"
    h = fetch(url)
    evs = []
    parts = re.split(r'(?=<a [^>]*href="https://www\.enterprise\.cam\.ac\.uk/events/[a-z0-9-]+/")', h)
    seen = set()
    for part in parts[1:]:
        link = re.match(r'<a [^>]*href="([^"]+)"', part).group(1)
        if link in seen:
            continue
        txt = strip_tags(part[:3000]).replace("\n", " ")
        start, end, all_day, m = find_when(txt[:400])
        if not start or not in_window(start):
            continue   # featured banners and "Details TBC" events have no date here
        seen.add(link)
        title = (txt[:m.start()] if m else txt[:100]).replace("Event ", "", 1).strip(" •")
        title = re.sub(r"^.*?(Programme|Event)\s+", "", title) if len(title) > 120 else title
        evs.append(make_event(
            id="ce-" + link.rstrip("/").split("/")[-1], title=title or link.rstrip("/").split("/")[-1].replace("-", " "),
            start=start, end=end, allDay=all_day, organiser="Cambridge Enterprise", url=link,
            source="Cambridge Enterprise", sourceGroup="Entrepreneurship",
        ))
    log(f"Cambridge Enterprise: {len(evs)} dated events")
    return _home(evs, "Entrepreneurship")


# --------------------------------------------------------------------------
# Societies: EA Cambridge (Luma calendar feed)
# --------------------------------------------------------------------------
def parse_ics(text):
    text = re.sub(r"\r?\n[ \t]", "", text)   # unfold
    out = []
    for block in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", text, re.S):
        ev = {}
        for line in block.strip().splitlines():
            if ":" not in line:
                continue
            k, v = line.split(":", 1)
            key = k.split(";")[0].upper()
            v = v.replace("\\n", "\n").replace("\\,", ",").replace("\;", ";").replace("\\\\", "\\")
            ev[key] = (v, k)
        out.append(ev)
    return out


def _ics_dt(val):
    v, k = val
    if "VALUE=DATE" in k or len(v) == 8:
        return dt.datetime.strptime(v[:8], "%Y%m%d"), True
    if v.endswith("Z"):
        return dt.datetime.strptime(v, "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.timezone.utc), False
    return dt.datetime.strptime(v[:15], "%Y%m%dT%H%M%S"), False


def scrape_ics(name, url, organiser, group, home, log, id_prefix):
    evs = []
    for ev in parse_ics(fetch(url)):
        if "DTSTART" not in ev:
            continue
        s, all_day = _ics_dt(ev["DTSTART"])
        e = _ics_dt(ev["DTEND"])[0] if "DTEND" in ev else None
        loc = ev.get("LOCATION", ("", ""))[0]
        desc = ev.get("DESCRIPTION", ("", ""))[0]
        link = ev.get("URL", ("", ""))[0] or (re.search(r"https://lu\.ma/\S+|https://luma\.com/\S+", desc) or [None])[0] or url
        evs.append(make_event(
            id=id_prefix + re.sub(r"\W", "", ev.get("UID", (link, ""))[0])[-24:], title=ev.get("SUMMARY", ("", ""))[0],
            start=to_local(s) if not all_day else f"{s.date()}T00:00",
            end=(to_local(e) if e and not all_day else (f"{s.date()}T23:59" if all_day else None)), allDay=all_day,
            venue="" if loc.startswith("http") else loc, organiser=organiser, url=link, booking=link,
            description=strip_tags(desc), source=name, sourceGroup=group,
        ))
    evs = [e for e in evs if in_window(e["start"])]
    log(f"{name}: {len(evs)} upcoming events")
    return _home(evs, home) if home else evs


# --------------------------------------------------------------------------
# Manually entered termcard events (societies that only publish images/Instagram)
# --------------------------------------------------------------------------
MANUAL_FILE = HERE / "termcards.json"
ORG_TOPICS = [
    (r"law soc|culs|law", "Law"),
    (r"leaps|enterprise|entrepreneur|e-?lab|judge|founder|startup|venture", "Entrepreneurship"),
    (r"effective altruism|\bEA\b", "Effective Altruism"),
    (r"union|politic|labour|conservative|cuca|liberal|cula|culc|debat", "Politics"),
]


def load_manual(log):
    if not MANUAL_FILE.exists():
        return []
    try:
        items = json.loads(MANUAL_FILE.read_text())
    except Exception as e:
        log(f"termcards.json could not be read: {e}")
        return []
    evs = []
    for i, it in enumerate(items):
        if not it.get("title") or not it.get("start"):
            continue
        ev = make_event(
            id=it.get("id") or f"manual-{i}", title=it["title"], start=it["start"][:16], end=(it.get("end") or "")[:16] or None,
            allDay=len(it["start"]) <= 10, venue=it.get("venue", ""), speaker=it.get("speaker", ""),
            organiser=it.get("organiser", ""), series=it.get("organiser", ""), description=it.get("description", ""),
            url=it.get("url") or "", source=f"Termcard: {it.get('organiser') or 'manual entry'}", sourceGroup="Manual",
            topics=it.get("topics") or None,
        )
        if len(it["start"]) <= 10:
            ev["start"], ev["end"] = it["start"][:10] + "T00:00", it["start"][:10] + "T23:59"
        if not ev["topics"]:
            org = it.get("organiser", "")
            for rx, topic in ORG_TOPICS:
                if re.search(rx, org, re.I):
                    ev["topics"] = [topic]
                    break
        ev["manual"] = True
        evs.append(ev)
    evs = [e for e in evs if e["start"][:10] >= str(TODAY - dt.timedelta(days=60))]
    log(f"Termcards (manual): {len(evs)} events")
    return evs

# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------
def _safe(fn, arg, log, default):
    try:
        return fn(arg)
    except Exception as e:
        log(f"  ! {fn.__name__}({arg}) failed: {e}")
        return default


def _norm_title(t):
    return re.sub(r"[^a-z0-9]", "", re.sub(r"[’']s\b", "", t.lower()))


STOP = {"the", "a", "an", "of", "and", "in", "on", "for", "to", "with", "at", "by", "lecture", "seminar", "talk", "event"}


def _tokens(t):
    return {w for w in re.findall(r"[a-z0-9]+", re.sub(r"[’']s\b", "", t.lower())) if w not in STOP}


def _same(a, b):
    na, nb = _norm_title(a["title"]), _norm_title(b["title"])
    if na == nb:
        return True
    short, long_ = sorted([na, nb], key=len)
    if len(short) >= 15 and short[:30] in long_:
        return True
    ta, tb = _tokens(a["title"]), _tokens(b["title"])
    if not ta or not tb:
        return False
    j = len(ta & tb) / len(ta | tb)
    same_time = a["start"] == b["start"]
    return j >= 0.6 or (same_time and j >= 0.35)


def dedupe(events):
    """Same event often appears on a dept site, talks.cam and Eventbrite: merge them."""
    priority = {"Manual": -1, "talks.cam": 0, "Department": 1, "Research centre": 1, "Entrepreneurship": 1,
                "Societies": 1, "Eventbrite": 2}
    events.sort(key=lambda e: priority.get(e["sourceGroup"], 9))
    out, by_day = [], {}
    for e in events:
        day = e["start"][:10]
        hit = next((o for o in by_day.get(day, []) if _same(o, e)), None)
        if hit is None:
            e["alsoListed"] = []
            by_day.setdefault(day, []).append(e)
            out.append(e)
        else:
            hit["alsoListed"].append({"source": e["source"], "url": e["url"]})
            hit["topics"] = sorted(set(hit["topics"]) | set(e["topics"]), key=list(TOPICS).index)
            if e.get("booking") and not hit.get("booking"):
                hit["booking"] = e["booking"]
            if hit["allDay"] and not e["allDay"]:
                hit.update(start=e["start"], end=e["end"], allDay=False)
            if not hit["description"]:
                hit["description"] = e["description"]
    return out


def write_data(data):
    (HERE / "events.json").write_text(json.dumps(data, ensure_ascii=False, indent=1))
    (HERE / "events.js").write_text("window.EVENT_DATA = " + json.dumps(data, ensure_ascii=False) + ";\n")
    write_calendars(data)


# --------------------------------------------------------------------------
# Calendar feeds (.ics) for subscribing in Apple / Google / Outlook Calendar
# --------------------------------------------------------------------------
CAL_DIR = HERE / "calendar"
VTIMEZONE_LONDON = """BEGIN:VTIMEZONE
TZID:Europe/London
BEGIN:DAYLIGHT
TZOFFSETFROM:+0000
TZOFFSETTO:+0100
TZNAME:BST
DTSTART:19700329T010000
RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=-1SU
END:DAYLIGHT
BEGIN:STANDARD
TZOFFSETFROM:+0100
TZOFFSETTO:+0000
TZNAME:GMT
DTSTART:19701025T020000
RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU
END:STANDARD
END:VTIMEZONE"""


def slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _ics_text(v):
    v = str(v or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
    return v.replace("\r\n", "\\n").replace("\n", "\\n")


def _fold(line):
    """Fold lines at 75 octets as the iCalendar standard requires."""
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return line
    parts, cur = [], b""
    for ch in line:
        b = ch.encode("utf-8")
        if len(cur) + len(b) > (75 if not parts else 74):
            parts.append(cur.decode("utf-8"))
            cur = b""
        cur += b
    parts.append(cur.decode("utf-8"))
    return "\r\n ".join(parts)


def _ics_local(iso):
    return iso.replace("-", "").replace(":", "")[:13] + "00"


def build_ics(events, name, stamp):
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Cambridge Events Calendar//EN", "CALSCALE:GREGORIAN",
             "METHOD:PUBLISH", f"X-WR-CALNAME:{_ics_text(name)}", "X-WR-TIMEZONE:Europe/London",
             "REFRESH-INTERVAL;VALUE=DURATION:PT6H", "X-PUBLISHED-TTL:PT6H", *VTIMEZONE_LONDON.split("\n")]
    for e in events:
        uid = re.sub(r"[^A-Za-z0-9._-]", "", e["id"])[:80] + "@cambridge-events"
        if e.get("allDay"):
            d1 = e["start"][:10].replace("-", "")
            last = dt.date.fromisoformat((e.get("end") or e["start"])[:10])
            d2 = (last + dt.timedelta(days=1)).strftime("%Y%m%d")
            when = [f"DTSTART;VALUE=DATE:{d1}", f"DTEND;VALUE=DATE:{d2}"]
        else:
            end = e.get("end") or to_local(dt.datetime.fromisoformat(e["start"]) + dt.timedelta(hours=1))
            when = [f"DTSTART;TZID=Europe/London:{_ics_local(e['start'])}",
                    f"DTEND;TZID=Europe/London:{_ics_local(end)}"]
        details = [
            f"Speaker: {e['speaker']}" if e.get("speaker") else "",
            e.get("series") or e.get("organiser") or "",
            "Topics: " + ", ".join(e.get("topics") or []) if e.get("topics") else "",
            f"Details: {e['url']}" if e.get("url") else "",
            f"Book: {e['booking']}" if e.get("booking") and e.get("booking") != e.get("url") else "",
            (e.get("description") or "")[:900],
        ]
        lines += ["BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{stamp}", *when,
                  f"SUMMARY:{_ics_text(e['title'])}"]
        if e.get("venue"):
            lines.append(f"LOCATION:{_ics_text(e['venue'])}")
        if e.get("url"):
            lines.append(f"URL:{e['url']}")
        if e.get("topics"):
            lines.append("CATEGORIES:" + ",".join(_ics_text(t) for t in e["topics"]))
        lines += [f"DESCRIPTION:{_ics_text(chr(10).join(x for x in details if x))}", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(l) for l in "\r\n".join(lines).split("\r\n")) + "\r\n"


def write_calendars(data):
    CAL_DIR.mkdir(exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    cutoff = str(TODAY - dt.timedelta(days=14))
    events = [e for e in data["events"] if (e.get("end") or e["start"])[:10] >= cutoff]
    feeds = {"all": ("Cambridge Events (all)", events)}
    for t in data.get("topics") or list(TOPICS):
        feeds[slug(t)] = (f"Cambridge Events: {t}", [e for e in events if t in (e.get("topics") or [])])
    for key, (name, evs) in feeds.items():
        (CAL_DIR / f"{key}.ics").write_bytes(build_ics(evs, name, stamp).encode("utf-8"))
    (CAL_DIR / "feeds.json").write_text(json.dumps(
        [{"file": f"calendar/{k}.ics", "name": n, "count": len(e)} for k, (n, e) in feeds.items()], indent=1))


def refresh_manual(log=print):
    """Re-merge termcards.json into events.json without re-scraping everything."""
    path = HERE / "events.json"
    data = json.loads(path.read_text()) if path.exists() else {"events": [], "sources": {}, "topics": list(TOPICS)}
    manual = load_manual(log)
    data["events"] = sorted([e for e in data["events"] if not e.get("manual")] + manual, key=lambda e: e["start"])
    data.setdefault("sources", {})["Termcards (manual)"] = {"ok": True, "count": len(manual)}
    data["topics"] = list(TOPICS)
    write_data(data)
    return data


def run(log=print):
    t0 = time.time()
    jobs = {
        "talks.cam": lambda: scrape_talks(log),
        "Bennett": lambda: scrape_bennett(log),
        "Eventbrite": lambda: scrape_eventbrite(log),
    }
    for name, base, path in DEPT_SITES:
        jobs[name] = (lambda n=name, b=base, p=path: scrape_drupal(n, b, p, log))
    for name, url, rx, home in WP_CENTRES:
        jobs[name] = (lambda n=name, u=url, r=rx, h=home: scrape_wp_centre(n, u, r, h, log))
    jobs["King's E-Lab"] = lambda: scrape_kings_elab(log)
    jobs["Judge Business School"] = lambda: scrape_jbs(log)
    jobs["Cambridge Enterprise"] = lambda: scrape_cambridge_enterprise(log)
    jobs["EA Cambridge"] = lambda: scrape_ics(
        "EA Cambridge", "https://api.lu.ma/ics/get?entity=calendar&id=cal-JiGNGa42nTFJvpF",
        "Effective Altruism Cambridge", "Societies", "Effective Altruism", log, "ea-")
    jobs["Termcards (manual)"] = lambda: load_manual(log)
    status, all_events = {}, []
    with cf.ThreadPoolExecutor(len(jobs)) as ex:
        futs = {ex.submit(fn): name for name, fn in jobs.items()}
        for f in cf.as_completed(futs):
            name = futs[f]
            try:
                evs = f.result()
                status[name] = {"ok": True, "count": len(evs)}
                all_events += evs
            except Exception as e:
                status[name] = {"ok": False, "error": str(e)}
                log(f"{name}: FAILED - {e}")
    events = dedupe([e for e in all_events if e["start"] and e["title"]])
    events.sort(key=lambda e: e["start"])
    data = {
        "generated": dt.datetime.now(LONDON).strftime("%Y-%m-%dT%H:%M"),
        "topics": list(TOPICS),
        "sources": status,
        "events": events,
    }
    write_data(data)
    log(f"Done: {len(events)} events in {time.time() - t0:.0f}s -> events.json")
    return data


if __name__ == "__main__":
    run(lambda m: print(m, flush=True))
    sys.exit(0)
