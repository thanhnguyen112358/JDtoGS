import argparse, datetime as dt, pytz, re, yaml, tldextract, sys, json
from dateutil import tz
from typing import Dict, Any, Optional, Tuple
import requests
from bs4 import BeautifulSoup
import gspread
from google.oauth2.service_account import Credentials
from pathlib import Path
import platform
# ---------------- Constants ----------------

ROOT = Path(__file__).parent.resolve()

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; JobLinkBot/1.0)"
}

FIELDS = [
    "AID",
    "Date",
    "Company Name",
    "Industry / Sector",
    "Position Title",
    "Period",
    "Application Link",
    "Contact Person",
    "Contact Email",
    "Contact Phone",
    "Source (Job Board / Referral / Career Fair)",
    "Application Status",
    "Next Follow-up Date",
    "Interview Dates",
    "Notes / Observations",
    "Resume Version Used",
    "Cover Letter Version Used",
    "Offer Received (Y/N)",
    "Offer Details",
    "Decision Made (Y/N)",
]

# ---------------- Config & Sheets ----------------

def load_config() -> Dict[str, Any]:
    cfg_path = ROOT / "config.yml"
    with open(cfg_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def load_industry_map() -> Dict[str,str]:
    path = ROOT / "industry_map.yml"
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    # lowercase keys
    return {str(k).lower(): v for k,v in data.items()}

def open_sheet(cfg: Dict[str,Any]):
    creds = Credentials.from_service_account_file(
        cfg["google"]["service_account_json"],
        scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(cfg["google"]["sheet_id"])

    ws_name = (cfg.get("google", {}) or {}).get("worksheet_name")
    if ws_name:
        try:
            ws = sh.worksheet(ws_name)  # exact tab title (case-sensitive)
        except gspread.WorksheetNotFound:
            ws = sh.add_worksheet(title=ws_name, rows=2000, cols=26)
            ws.append_row(FIELDS, value_input_option="USER_ENTERED")  # write header on new tab
    else:
        ws = sh.sheet1  # fallback to first tab

    # Ensure header exists on existing tab
    first_row = ws.row_values(1)
    if not first_row:
        ws.append_row(FIELDS, value_input_option="USER_ENTERED")

    return ws

def load_list(cfg, key):
    v = (cfg.get(key) or [])
    return [str(x) for x in v]

def choose_first_match(text: str, rules: dict, allowed: list) -> str:
    """Return the first allowed label whose keyword appears in text."""
    lt = (text or "").lower()
    for label in allowed:
        kws = (rules.get(label) or [])
        for kw in kws:
            if kw.lower() in lt:
                return label
    return ""

def normalize_industry(raw: str, allowed: list, aliases: dict) -> str:
    if not raw:
        return ""
    lt = raw.lower().strip()
    # exact match
    for a in allowed:
        if lt == a.lower():
            return a
    # alias map
    for k, v in (aliases or {}).items():
        if k.lower() == lt:
            return v
    # partial alias (contains)
    for k, v in (aliases or {}).items():
        if k.lower() in lt:
            return v
    return ""

def classify_industry(title: str, company: str, url: str, cfg: dict) -> str:
    allowed = load_list(cfg, "industry_allowed")
    aliases = (cfg.get("industry_aliases") or {})
    rules   = (cfg.get("industry_rules") or {})

    # 1) rules by priority order
    label = choose_first_match(" ".join([title or "", company or "", url or ""]), rules, allowed)
    if label:
        return label
    # 2) alias normalization from any free text
    label = normalize_industry(" ".join([title or "", company or ""]), allowed, aliases)
    if label:
        return label
    return ""  # let CLI or default handle it

# ---------------- Utils ----------------

def now_str(tz_name: str) -> str:
    tzinfo = pytz.timezone(tz_name)
    return dt.datetime.now(tzinfo).strftime("%Y-%m-%d %H:%M:%S")

def guess_period(dt_obj: dt.datetime) -> str:
    m = dt_obj.month
    if 1 <= m <= 4: return "Spring"
    if 5 <= m <= 8: return "Summer"
    return "Fall"

def extract_domain(url: str) -> str:
    ext = tldextract.extract(url)
    return ".".join(p for p in [ext.domain, ext.suffix] if p)

def first_nonempty(*vals):
    for v in vals:
        if v and str(v).strip():
            return str(v).strip()
    return ""

def find_id_from_url(url: str) -> str:
    import re
    # Prefer Workday-style R-numbers: _R362134 or -R362134
    m = re.search(r'[_-]R(\d{3,})\b', url, re.IGNORECASE)
    if m:
        return f"R{m.group(1)}"
    # Greenhouse numeric
    m = re.search(r"/jobs/(\d+)", url)
    if m:
        return m.group(1)
    # Lever UUID-ish (fallback)
    m = re.search(r"/jobs?/([a-z0-9-]{8,})", url, re.IGNORECASE)
    if m:
        return m.group(1)
    return ""

import json, re, tldextract
from bs4 import BeautifulSoup

def hostname_from_url(url: str) -> str:
    # full host, e.g., "msd.wd5.myworkdayjobs.com"
    from urllib.parse import urlparse
    return urlparse(url).hostname or ""

def slug_to_name(slug: str) -> str:
    # "procter-and-gamble" -> "Procter And Gamble"
    s = re.sub(r"[^a-zA-Z0-9]+", " ", slug).strip()
    return " ".join(w.capitalize() for w in s.split())

def company_from_jsonld(soup: BeautifulSoup) -> str:
    # Try schema.org JobPosting → hiringOrganization.name
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "{}")
        except Exception:
            continue
        # could be list or single dict
        candidates = data if isinstance(data, list) else [data]
        for obj in candidates:
            if not isinstance(obj, dict): 
                continue
            if obj.get("@type", "").lower() == "jobposting":
                org = obj.get("hiringOrganization") or obj.get("hiringorganization")
                if isinstance(org, dict) and org.get("name"):
                    return org["name"].strip()
    return ""

def company_from_meta(soup: BeautifulSoup) -> str:
    # Try site metadata
    site = soup.find("meta", attrs={"property": "og:site_name"})
    if site and site.get("content"):
        name = site["content"].strip()
        # Filter generic hosts like "Workday", "LinkedIn"
        if name.lower() not in {"workday", "linkedin", "greenhouse", "lever"}:
            return name
    return ""

def company_from_url_pattern(url: str) -> str:
    # Greenhouse: boards.greenhouse.io/{company}/jobs/...
    m = re.search(r"boards\.greenhouse\.io/([^/]+)/?", url, re.IGNORECASE)
    if m:
        return slug_to_name(m.group(1))
    # Lever: jobs.lever.co/{company}/...
    m = re.search(r"jobs\.lever\.co/([^/]+)/?", url, re.IGNORECASE)
    if m:
        return slug_to_name(m.group(1))
    # Workday: {tenant}.wdX.myworkdayjobs.com → use tenant as a hint
    m = re.search(r"https?://([a-z0-9-]+)\.wd\d+\.myworkdayjobs\.com", url, re.IGNORECASE)
    if m:
        # Often an acronym; we keep as-is and let company_map remap
        return slug_to_name(m.group(1))
    return ""

# ---------------- Adapters ----------------

def parse_opengraph(soup: BeautifulSoup) -> Tuple[str,str]:
    """Return (title, site_name) from OG tags if present."""
    og_title = soup.find("meta", property="og:title")
    og_site = soup.find("meta", property="og:site_name")
    return (
        og_title["content"].strip() if og_title and og_title.get("content") else "",
        og_site["content"].strip() if og_site and og_site.get("content") else "",
    )

def adapter_greenhouse(soup: BeautifulSoup) -> Dict[str,str]:
    out = {}
    header = soup.select_one("div#app_body h1.app-title, h1")
    if header:
        out["title"] = header.get_text(strip=True)
    comp = soup.select_one("div#header .company-name, a.company-name, a[href*='greenhouse.io/']")
    if comp:
        out["company"] = comp.get_text(strip=True)
    loc = soup.select_one(".location, .location-and-id, .metadata .location")
    if loc:
        out["location"] = loc.get_text(strip=True)
    return out

def adapter_lever(soup: BeautifulSoup) -> Dict[str,str]:
    out = {}
    header = soup.select_one("h2.posting-headline, .posting-headline h2, h1")
    if header:
        out["title"] = header.get_text(strip=True)
    comp = soup.select_one(".company-name, .posting-categories .category")
    # company often in OG site_name
    loc = soup.select_one(".location, .posting-categories .location")
    if loc:
        out["location"] = loc.get_text(strip=True)
    return out

def adapter_workday(soup: BeautifulSoup) -> Dict[str,str]:
    out = {}
    h1 = soup.find("h1")
    if h1:
        out["title"] = h1.get_text(strip=True)
    return out

def adapter_linkedin(soup: BeautifulSoup) -> Dict[str,str]:
    out = {}
    t, site = parse_opengraph(soup)
    if t:
        out["title"] = t.split("|")[0].strip()
    return out

ADAPTERS = {
    "greenhouse.io": adapter_greenhouse,
    "lever.co": adapter_lever,
    "workday": adapter_workday,
    "linkedin.com": adapter_linkedin,
}

def choose_adapter(domain: str):
    for key, fn in ADAPTERS.items():
        if key in domain:
            return fn
    return None

def generic_adapter(soup: BeautifulSoup) -> Dict[str,str]:
    out = {}
    t, site = parse_opengraph(soup)
    if t:
        parts = [p.strip() for p in t.split(" - ") if p.strip()]
        if parts:
            out["title"] = parts[0]
            if len(parts) > 1:
                out["company"] = parts[1]
    if not out.get("title"):
        title_tag = soup.find("title")
        if title_tag:
            out["title"] = title_tag.get_text(strip=True)
    return out

def adapter_workday(soup: BeautifulSoup) -> Dict[str,str]:
    out = {}
    h1 = soup.find("h1")
    if h1:
        out["title"] = h1.get_text(strip=True)
    # try meta description (twitter/og)
    for sel in [
        ('meta', {'name':'twitter:description'}),
        ('meta', {'property':'og:description'})
    ]:
        tag = soup.find(*sel)
        if tag and tag.get('content'):
            txt = ' '.join(tag['content'].split())
            # crude pull like "... Location: Rahway, NJ, United States ..."
            import re
            m = re.search(r'Location:\s*([^.|]+)', txt, re.IGNORECASE)
            if m:
                out["location"] = m.group(1).strip()
                break
    return out

# ---------------- Core ----------------

def scrape(url: str) -> Dict[str,str]:
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    domain = extract_domain(url)
    adapter = choose_adapter(domain)
    out = adapter(soup) if adapter else generic_adapter(soup)

    # Title fallback from OG
    og_t, og_site = parse_opengraph(soup)
    out.setdefault("title", og_t.split("|")[0].strip() if og_t else "")

    # Company resolution (robust)
    cfg = load_config()  # safe to call here
    resolved_company = resolve_company(url, soup, cfg)

    # If adapter/meta left company empty OR generic (e.g., "Workday"), use resolved_company
    curr_company = (out.get("company") or "").strip()
    if not curr_company or curr_company.lower() in GENERIC_SITES:
        out["company"] = resolved_company
    else:
        out["company"] = curr_company

    # Optional: keep for debugging
    out["__resolved_company"] = resolved_company

    # Clean whitespace
    for k in list(out.keys()):
        if isinstance(out[k], str):
            out[k] = re.sub(r"\s+", " ", out[k]).strip()

    return out
GENERIC_SITES = {"workday", "linkedin", "greenhouse", "lever"}

def infer_industry(industry_map: Dict[str,str], text: str) -> str:
    if not text:
        return ""
    lt = text.lower()
    for key, label in industry_map.items():
        if key in lt:
            return label
    return ""

def resolve_company(url: str, soup: BeautifulSoup, cfg: dict) -> str:
    host = hostname_from_url(url)
    # 1) explicit map (best)
    mapped = (cfg.get("company_map") or {}).get(host)
    if mapped:
        return mapped

    # 2) JSON-LD schema.org
    c = company_from_jsonld(soup)
    if c:
        return c

    # 3) Meta/OpenGraph site name (filtered)
    c = company_from_meta(soup)
    if c:
        return c

    # 4) URL heuristics (Greenhouse/Lever/Workday tenant)
    c = company_from_url_pattern(url)
    if c:
        return c

    # 5) last resort: use registrable domain (e.g., "merck.com" -> "Merck")
    ext = tldextract.extract(url)
    if ext.domain:
        return ext.domain.capitalize()

    return ""

def main():
    parser = argparse.ArgumentParser(description="Add job posting to Google Sheet by URL.")
    sub = parser.add_subparsers(dest="cmd")

    p = sub.add_parser("add", help="Add a job by URL")
    p.add_argument("url")
    p.add_argument("--source", default=None)
    p.add_argument("--status", default=None)
    p.add_argument("--industry", default=None)
    p.add_argument("--period", default=None)
    p.add_argument("--company", default=None)
    p.add_argument("--title", default=None)
    p.add_argument("--location", default=None)
    p.add_argument("--notes", default=None)
    p.add_argument("--contact-person", dest="contact_person", default=None)
    p.add_argument("--contact-email", dest="contact_email", default=None)
    p.add_argument("--contact-phone", dest="contact_phone", default=None)
    p.add_argument("--next-followup", dest="next_followup", default=None)   # free text/date
    p.add_argument("--interview-dates", dest="interview_dates", default=None)
    p.add_argument("--resume-version", dest="resume_version", default=None)
    p.add_argument("--cover-letter-version", dest="cover_letter_version", default=None)
    p.add_argument("--offer-received", dest="offer_received", default=None) # e.g., Y/N
    p.add_argument("--offer-details", dest="offer_details", default=None)
    p.add_argument("--decision-made", dest="decision_made", default=None)   # e.g., Y/N


    args = parser.parse_args()
    if args.cmd != "add":
        parser.print_help()
        sys.exit(0)

    cfg = load_config()
    company_map = (cfg.get("company_map") or {})
    industry_rules = (cfg.get("industry_rules") or {})

    ws = open_sheet(cfg)
    ind_map = load_industry_map()

    tz_name = cfg.get("defaults",{}).get("timezone","America/New_York")
    tzinfo = pytz.timezone(tz_name)
    now = dt.datetime.now(tzinfo)
    date_fmt = (cfg.get("defaults", {}) or {}).get("date_format") or "%Y-%m-%d %H:%M:%S"

    # pick format from config or default ISO
    date_fmt = (cfg.get("defaults", {}) or {}).get("date_format") or "%Y-%m-%d %H:%M:%S"

    # Normalize Unix %-flags to Windows %#-flags if needed
    if platform.system() == "Windows":
        date_fmt = (date_fmt
            .replace("%-d", "%#d")
            .replace("%-m", "%#m")
            .replace("%-H", "%#H")
            .replace("%-I", "%#I")
            .replace("%-M", "%#M")
            .replace("%-S", "%#S"))

    # Final fallback if the format is invalid
    try:
        date_applied = now.strftime(date_fmt)
    except ValueError:
        date_applied = now.strftime("%Y-%m-%d %H:%M:%S")

    meta = {}
    try:
        meta = scrape(args.url)
    except Exception as e:
        meta = {}

    raw_title = meta.get("title","")
    raw_company = meta.get("company","")
    resolved_company = meta.get("__resolved_company","")

    raw_location = meta.get("location","")

    company = (args.company or raw_company or resolved_company or "").strip()
    title = (args.title or raw_title or "").strip()
    location = (args.location or raw_location or "").strip()

    def infer_from_rules(rules: Dict[str,str], text: str) -> str:
        if not text: return ""
        lt = text.lower()
        for key, label in rules.items():
            if key in lt:
                return label
        return ""

    # previous:
    # industry = (args.industry or infer_industry(ind_map, " ".join([title, company])))

    allowed = load_list(cfg, "industry_allowed")
    aliases = (cfg.get("industry_aliases") or {})

    industry = (
        (args.industry and normalize_industry(args.industry, allowed, aliases))  # CLI override normalized
        or classify_industry(title, company, args.url, cfg)                      # rules/aliases
        or ""                                                                    # fallback
    )


    period = args.period or guess_period(now)

    app_id = find_id_from_url(args.url)

    source = args.source or cfg.get("defaults",{}).get("source","")
    status = args.status or cfg.get("defaults",{}).get("status","")
    notes = args.notes or ""

    domain = extract_domain(args.url)

    # date formatting you already patched:
    # date_applied = now.strftime(date_fmt)

    aid = find_id_from_url(args.url)  # e.g., R362134

    row = [
        aid,                               # AID
        date_applied,                      # Date
        company,                           # Company Name
        industry,                          # Industry / Sector
        title,                             # Position Title
        period,                            # Period
        args.url,                          # Application Link
        (args.contact_person or ""),       # Contact Person
        (args.contact_email or ""),        # Contact Email
        (args.contact_phone or ""),        # Contact Phone
        (args.source or source),           # Source (Job Board / Referral / Career Fair)
        (args.status or status),           # Application Status
        (args.next_followup or ""),        # Next Follow-up Date
        (args.interview_dates or ""),      # Interview Dates
        (args.notes or notes),             # Notes / Observations
        (args.resume_version or ""),       # Resume Version Used
        (args.cover_letter_version or ""), # Cover Letter Version Used
        (args.offer_received or ""),       # Offer Received (Y/N)
        (args.offer_details or ""),        # Offer Details
        (args.decision_made or ""),        # Decision Made (Y/N)
    ]


    ws.append_row(row, value_input_option="USER_ENTERED")
    print("✓ Added row to sheet:", row)

if __name__ == "__main__":
    main()
