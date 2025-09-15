#!/usr/bin/env python3
"""Job Description to Google Sheets (JDtoGS)

A command-line tool that scrapes job posting information from URLs and adds them to a Google Sheet.
This tool helps automate job application tracking by parsing job details and organizing them
systematically in a spreadsheet.

Features:
- Scrapes job posting details (title, company, location) from various job sites
- Smart detection of company names, job titles, and industries
- Detects application periods (Summer, Fall, etc.) from job descriptions
- Supports multiple job board adapters (Workday, Greenhouse, Lever, LinkedIn, etc.)
- Configurable mappings for companies and industries
- Customizable field formatting and timezone settings

Requirements:
- Python 3.6+
- Google API credentials (service account JSON)
- Required Python packages: gspread, google-auth, requests, beautifulsoup4, 
    pytz, pyyaml, tldextract

Setup:
1. Create a config.yml file in the same directory as the script
2. Configure Google Sheets integration with a service account
3. Optionally create an industry_map.yml for company/industry mappings

Usage:
        python app.py add <job_url> [options]

Options:
        --source             Job source (e.g., LinkedIn, Referral)
        --status             Application status (e.g., Applied, Interview)
        --industry           Industry/sector
        --period             Application period (e.g., Summer, Fall)
        --company            Company name (overrides detected)
        --title              Position title (overrides detected)
        --location           Job location
        --notes              Additional notes
        --contact-person     Contact name
        --contact-email      Contact email
        --contact-phone      Contact phone
        --next-followup      Next follow-up date
        --interview-dates    Interview dates
        --resume-version     Resume version used
        --cover-letter-version  Cover letter version used
        --offer-received     Offer status (Y/N)
        --offer-details      Offer details
        --decision-made      Decision status (Y/N)

Configuration:
        config.yml should contain:
        - Google Sheets integration details
        - Default timezone
        - Industry mappings
        - Company name mappings
        - Default field values

Example:
        python app.py add https://company.com/jobs/12345 --source "LinkedIn" --status "Applied"

Author: Unknown
License: Unknown
# -*- coding: utf-8 -*-
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import platform
import re
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import gspread
import pytz
import requests
import tldextract
import yaml
from bs4 import BeautifulSoup
from google.oauth2.service_account import Credentials

# ---------------- Constants ----------------

ROOT = Path(__file__).parent.resolve()

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; JobLinkBot/1.1; +https://example.com/bot)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.7",
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

SEASON_WORDS = {
    "spring": ["spring"],
    "summer": ["summer", "may-aug", "may to aug", "may through aug"],
    "fall": ["fall", "autumn", "sep-dec", "sept-dec"],
    "winter": ["winter", "dec-mar", "jan-mar"],
}

GENERIC_SITES = {"workday", "linkedin", "greenhouse", "lever"}

# ---------------- Date/Season helpers ----------------

def month_to_season(m: int) -> str:
    if 1 <= m <= 4:
        return "Spring"
    if 5 <= m <= 8:
        return "Summer"
    if 9 <= m <= 12:
        return "Fall"
    return ""


def detect_period_from_text(*texts: str) -> str:
    blob = " ".join([t or "" for t in texts]).lower()
    # direct season words
    for season, keys in SEASON_WORDS.items():
        for k in keys:
            if k in blob:
                return season.capitalize()
    # simple month-window guess from text
    months = [
        "jan",
        "feb",
        "mar",
        "apr",
        "may",
        "jun",
        "jul",
        "aug",
        "sep",
        "sept",
        "oct",
        "nov",
        "dec",
    ]
    if any(m in blob for m in months):
        if any(m in blob for m in ["may", "jun", "jul", "aug"]):
            return "Summer"
        if any(m in blob for m in ["mar", "apr"]):
            return "Spring"
        if any(m in blob for m in ["sep", "sept", "oct", "nov", "dec"]):
            return "Fall"
        if "jan" in blob or "feb" in blob:
            return "Spring"  # heuristic
    return ""


# ---------------- Config & Sheets ----------------

def load_config() -> Dict[str, Any]:
    cfg_path = ROOT / "config.yml"
    with open(cfg_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_industry_map() -> Dict[str, str]:
    path = ROOT / "industry_map.yml"
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return {str(k).lower(): v for k, v in data.items()}


def _ensure_header(ws) -> None:
    try:
        first_row = ws.row_values(1)
    except Exception:
        first_row = []
    if not first_row:
        ws.append_row(FIELDS, value_input_option="USER_ENTERED")
    elif first_row != FIELDS:
        # Try to update header in-place if misaligned
        ws.update("A1", [FIELDS])


def open_sheet(cfg: Dict[str, Any]):
    creds = Credentials.from_service_account_file(
        cfg["google"]["service_account_json"],
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(cfg["google"]["sheet_id"])

    ws_name = (cfg.get("google", {}) or {}).get("worksheet_name")
    if ws_name:
        try:
            ws = sh.worksheet(ws_name)
        except gspread.WorksheetNotFound:
            ws = sh.add_worksheet(title=ws_name, rows=2000, cols=26)
    else:
        ws = sh.sheet1

    _ensure_header(ws)
    return ws


def load_list(cfg: Dict[str, Any], key: str) -> list[str]:
    v = cfg.get(key) or []
    return [str(x) for x in v]


# ---------------- Text/Parsing helpers ----------------

def choose_first_match(text: str, rules: dict, allowed: list[str]) -> str:
    lt = (text or "").lower()
    for label in allowed:
        kws = rules.get(label) or []
        for kw in kws:
            if kw.lower() in lt:
                return label
    return ""


def normalize_industry(raw: str, allowed: list[str], aliases: dict) -> str:
    if not raw:
        return ""
    lt = raw.lower().strip()
    for a in allowed:  # exact match
        if lt == a.lower():
            return a
    for k, v in (aliases or {}).items():  # exact alias
        if k.lower() == lt:
            return v
    for k, v in (aliases or {}).items():  # contains alias
        if k.lower() in lt:
            return v
    return ""


def classify_industry(title: str, company: str, url: str, cfg: dict) -> str:
    allowed = load_list(cfg, "industry_allowed")
    aliases = cfg.get("industry_aliases") or {}
    rules = cfg.get("industry_rules") or {}

    label = choose_first_match(" ".join([title or "", company or "", url or ""]), rules, allowed)
    if label:
        return label
    label = normalize_industry(" ".join([title or "", company or ""]).strip(), allowed, aliases)
    return label or ""


def now_str(tz_name: str) -> str:
    tzinfo = pytz.timezone(tz_name)
    return dt.datetime.now(tzinfo).strftime("%Y-%m-%d %H:%M:%S")


def guess_period(dt_obj: dt.datetime) -> str:
    return month_to_season(dt_obj.month) or ""


def extract_domain(url: str) -> str:
    ext = tldextract.extract(url)
    return ".".join(p for p in [ext.domain, ext.suffix] if p)


def find_id_from_url(url: str) -> str:
    # Workday style: -R362134 / _R362134 / R-362134
    m = re.search(r"(?:[_-]|\b)R[-_]?(\d{3,})(?:\b|/|$)", url, re.IGNORECASE)
    if m:
        return f"R{m.group(1)}"
    # Greenhouse numeric: /jobs/1234567
    m = re.search(r"/jobs/(\d+)(?:\b|/|$)", url)
    if m:
        return m.group(1)
    # Lever UUID-ish slug after /jobs or /postings
    m = re.search(r"/(?:jobs?|postings)/([a-z0-9-]{8,})(?:\b|/|$)", url, re.IGNORECASE)
    if m:
        return m.group(1)
    return ""


def hostname_from_url(url: str) -> str:
    from urllib.parse import urlparse
    return urlparse(url).hostname or ""


def slug_to_name(slug: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", " ", slug).strip()
    return " ".join(w.capitalize() for w in s.split())


def company_from_jsonld(soup: BeautifulSoup) -> str:
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "{}")
        except Exception:
            continue
        candidates = data if isinstance(data, list) else [data]
        for obj in candidates:
            if not isinstance(obj, dict):
                continue
            if str(obj.get("@type", "")).lower() == "jobposting":
                org = obj.get("hiringOrganization") or obj.get("hiringorganization")
                if isinstance(org, dict) and org.get("name"):
                    return org["name"].strip()
    return ""


def company_from_meta(soup: BeautifulSoup) -> str:
    site = soup.find("meta", attrs={"property": "og:site_name"})
    if site and site.get("content"):
        name = site["content"].strip()
        if name.lower() not in GENERIC_SITES:
            return name
    return ""


def company_from_url_pattern(url: str) -> str:
    m = re.search(r"boards\.greenhouse\.io/([^/]+)/?", url, re.IGNORECASE)
    if m:
        return slug_to_name(m.group(1))
    m = re.search(r"jobs\.lever\.co/([^/]+)/?", url, re.IGNORECASE)
    if m:
        return slug_to_name(m.group(1))
    m = re.search(r"https?://([a-z0-9-]+)\.wd\d+\.myworkdayjobs\.com", url, re.IGNORECASE)
    if m:
        return slug_to_name(m.group(1))
    return ""


# ---------------- Adapters ----------------

def parse_opengraph(soup: BeautifulSoup) -> Tuple[str, str]:
    og_title = soup.find("meta", property="og:title")
    og_site = soup.find("meta", property="og:site_name")
    return (
        og_title["content"].strip() if og_title and og_title.get("content") else "",
        og_site["content"].strip() if og_site and og_site.get("content") else "",
    )


def adapter_greenhouse(soup: BeautifulSoup) -> Dict[str, str]:
    out: Dict[str, str] = {}
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


def adapter_lever(soup: BeautifulSoup) -> Dict[str, str]:
    out: Dict[str, str] = {}
    header = soup.select_one("h2.posting-headline, .posting-headline h2, h1")
    if header:
        out["title"] = header.get_text(strip=True)
    loc = soup.select_one(".location, .posting-categories .location")
    if loc:
        out["location"] = loc.get_text(strip=True)
    return out


def adapter_workday(soup: BeautifulSoup) -> Dict[str, str]:
    out: Dict[str, str] = {}
    h1 = soup.find("h1")
    if h1:
        out["title"] = h1.get_text(strip=True)
    for sel in [
        ("meta", {"name": "twitter:description"}),
        ("meta", {"property": "og:description"}),
    ]:
        tag = soup.find(*sel)
        if tag and tag.get("content"):
            txt = " ".join(tag["content"].split())
            m = re.search(r"Location:\s*([^.|]+)", txt, re.IGNORECASE)
            if m:
                out["location"] = m.group(1).strip()
                break
    return out


def adapter_linkedin(soup: BeautifulSoup) -> Dict[str, str]:
    out: Dict[str, str] = {}
    t, _ = parse_opengraph(soup)
    if t:
        out["title"] = t.split("|")[0].strip()
    return out


def adapter_generic(soup: BeautifulSoup) -> Dict[str, str]:
    out: Dict[str, str] = {}
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


def choose_adapter(domain: str):
    for key, fn in ADAPTERS.items():
        if key in domain:
            return fn
    return None


ADAPTERS = {
    "myworkdayjobs.com": adapter_workday,
    "greenhouse.io": adapter_greenhouse,
    "lever.co": adapter_lever,
    "linkedin.com": adapter_linkedin,
    "smartrecruiters.com": adapter_generic,
    "icims.com": adapter_generic,
    "workable.com": adapter_generic,
    "ashbyhq.com": adapter_generic,
    "bamboohr.com": adapter_generic,
}


# ---------------- Core scraping/resolution ----------------

def scrape(url: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise RuntimeError(f"HTTP error fetching URL: {e}") from e

    soup = BeautifulSoup(resp.text, "html.parser")
    domain = extract_domain(url)

    adapter = choose_adapter(domain)
    out = adapter(soup) if adapter else adapter_generic(soup)

    # Title fallback from OG
    og_t, _ = parse_opengraph(soup)
    out.setdefault("title", og_t.split("|")[0].strip() if og_t else "")

    # Company resolution
    cfg = load_config()
    resolved_company = resolve_company(url, soup, cfg)

    curr_company = (out.get("company") or "").strip()
    if not curr_company or curr_company.lower() in GENERIC_SITES:
        out["company"] = resolved_company
    else:
        out["company"] = curr_company

    out["__resolved_company"] = resolved_company  # for debugging

    # Clean whitespace
    for k, v in list(out.items()):
        if isinstance(v, str):
            out[k] = re.sub(r"\s+", " ", v).strip()

    return out


def infer_industry(industry_map: Dict[str, str], text: str) -> str:
    if not text:
        return ""
    lt = text.lower()
    for key, label in industry_map.items():
        if key in lt:
            return label
    return ""


def resolve_company(url: str, soup: BeautifulSoup, cfg: dict) -> str:
    host = hostname_from_url(url)

    mapped = (cfg.get("company_map") or {}).get(host)
    if mapped:
        return mapped

    c = company_from_jsonld(soup)
    if c:
        return c

    c = company_from_meta(soup)
    if c:
        return c

    c = company_from_url_pattern(url)
    if c:
        return c

    ext = tldextract.extract(url)
    if ext.domain:
        return ext.domain.capitalize()

    return ""


# ---------------- CLI ----------------

def _safe_format_now(fmt: str, tzinfo: pytz.BaseTzInfo) -> str:
    now = dt.datetime.now(tzinfo)
    if platform.system() == "Windows":
        fmt = (
            fmt.replace("%-d", "%#d")
            .replace("%-m", "%#m")
            .replace("%-H", "%#H")
            .replace("%-I", "%#I")
            .replace("%-M", "%#M")
            .replace("%-S", "%#S")
        )
    try:
        return now.strftime(fmt)
    except ValueError:
        return now.strftime("%Y-%m-%d %H:%M:%S")


def main() -> None:
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
    p.add_argument("--next-followup", dest="next_followup", default=None)
    p.add_argument("--interview-dates", dest="interview_dates", default=None)
    p.add_argument("--resume-version", dest="resume_version", default=None)
    p.add_argument("--cover-letter-version", dest="cover_letter_version", default=None)
    p.add_argument("--offer-received", dest="offer_received", default=None)
    p.add_argument("--offer-details", dest="offer_details", default=None)
    p.add_argument("--decision-made", dest="decision_made", default=None)

    args = parser.parse_args()

    if args.cmd != "add":
        parser.print_help()
        sys.exit(0)

    # Load config/state
    try:
        cfg = load_config()
    except FileNotFoundError:
        print("ERROR: config.yml not found next to the script.", file=sys.stderr)
        sys.exit(2)
    except Exception as e:
        print(f"ERROR: Failed to load config.yml: {e}", file=sys.stderr)
        sys.exit(2)

    try:
        ws = open_sheet(cfg)
    except Exception as e:
        print(f"ERROR: Could not open Google Sheet: {e}", file=sys.stderr)
        sys.exit(2)

    tz_name = cfg.get("defaults", {}).get("timezone", "America/New_York")
    tzinfo = pytz.timezone(tz_name)

    date_fmt = (cfg.get("defaults", {}) or {}).get("date_format") or "%Y-%m-%d %H:%M:%S"
    date_applied = _safe_format_now(date_fmt, tzinfo)

    # Scrape metadata (best-effort)
    meta: Dict[str, str] = {}
    try:
        meta = scrape(args.url)
    except Exception as e:
        print(f"WARN: scrape failed: {e}", file=sys.stderr)
        meta = {}

    raw_title = meta.get("title", "")
    raw_company = meta.get("company", "")
    resolved_company = meta.get("__resolved_company", "")
    raw_location = meta.get("location", "")

    company = (args.company or raw_company or resolved_company or "").strip()
    title = (args.title or raw_title or "").strip()
    location = (args.location or raw_location or "").strip()

    notes = args.notes or ""

    # Industry classification
    allowed = load_list(cfg, "industry_allowed")
    aliases = (cfg.get("industry_aliases") or {})

    industry = (
        (args.industry and normalize_industry(args.industry, allowed, aliases))
        or classify_industry(title, company, args.url, cfg)
        or ""
    )

    # Period detection after notes are known
    period = (
        (args.period and args.period.capitalize())
        or detect_period_from_text(raw_title, args.url, notes)
        or guess_period(dt.datetime.now(tzinfo))
    )

    aid = find_id_from_url(args.url)

    source = args.source or cfg.get("defaults", {}).get("source", "")
    status = args.status or cfg.get("defaults", {}).get("status", "")

    row = [
        aid,  # AID
        date_applied,  # Date
        company,  # Company Name
        industry,  # Industry / Sector
        title,  # Position Title
        period,  # Period
        args.url,  # Application Link
        (args.contact_person or ""),  # Contact Person
        (args.contact_email or ""),  # Contact Email
        (args.contact_phone or ""),  # Contact Phone
        (args.source or source),  # Source (Job Board / Referral / Career Fair)
        (args.status or status),  # Application Status
        (args.next_followup or ""),  # Next Follow-up Date
        (args.interview_dates or ""),  # Interview Dates
        (args.notes or notes),  # Notes / Observations
        (args.resume_version or ""),  # Resume Version Used
        (args.cover_letter_version or ""),  # Cover Letter Version Used
        (args.offer_received or ""),  # Offer Received (Y/N)
        (args.offer_details or ""),  # Offer Details
        (args.decision_made or ""),  # Decision Made (Y/N)
    ]

    try:
        ws.append_row(row, value_input_option="USER_ENTERED")
    except Exception as e:
        print(f"ERROR: Failed to append row: {e}", file=sys.stderr)
        sys.exit(2)

    print("\u2713 Added row to sheet:", row)


if __name__ == "__main__":
    main()