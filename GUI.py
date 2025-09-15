# Streamlit GUI for JobLinkBot
# ------------------------------------------------------------
# This app wraps the logic in joblinkbot_refactor.py with a friendly UI.
# Put this file in the same directory as joblinkbot_refactor.py and config.yml
# then run:
#   pip install -r requirements.txt
#   streamlit run app.py
#
# Notes:
# - The app reads Google creds and Sheet info from config.yml (see the refactor file's header for a skeleton).
# - Click "Scrape URL" to auto-fill Title/Company/Location; edit any field before saving.
# - Click "Append to Google Sheet" to write a new row.

import streamlit as st
import datetime as dt
import pytz
import traceback
from typing import Dict

# Local imports
import joblinkbot_refactor as core

st.set_page_config(page_title="JobLinkBot GUI", page_icon="🗂️", layout="centered")

# ---------------- Sidebar: Config & Help ----------------
with st.sidebar:
    st.title("⚙️ Settings")
    st.markdown(
        "This GUI uses **config.yml** next to the script. Update your Google Sheet ID, worksheet name, and service account JSON path there."
    )
    if st.button("Reload config.yml"):
        st.cache_data.clear()
        st.cache_resource.clear()

@st.cache_resource(show_spinner=False)
def load_cfg() -> Dict:
    return core.load_config()

try:
    cfg = load_cfg()
except Exception as e:
    st.error(f"Failed to load config.yml: {e}")
    st.stop()

# Prepare basics from config
tz_name = (cfg.get("defaults", {}) or {}).get("timezone", "America/New_York")
allowed_industries = [str(x) for x in (cfg.get("industry_allowed") or [])]

# ---------------- Main: Form ----------------
st.title("🗂️ JobLinkBot — Add Job to Sheet")
st.caption("Paste a posting URL, auto-fill details, tweak, then save to Google Sheets.")

with st.form("job_form", clear_on_submit=False):
    url = st.text_input("Application Link (URL)", placeholder="https://boards.greenhouse.io/... or https://...myworkdayjobs.com/...", help="Paste the full job posting URL.")

    col1, col2 = st.columns(2)
    with col1:
        source = st.text_input("Source", value=(cfg.get("defaults", {}) or {}).get("source", ""))
        status = st.text_input("Application Status", value=(cfg.get("defaults", {}) or {}).get("status", ""))
        industry = st.selectbox("Industry / Sector", options=[""] + allowed_industries, index=0)
    with col2:
        period = st.selectbox("Period", options=["", "Spring", "Summer", "Fall"], index=0)
        offer_received = st.selectbox("Offer Received (Y/N)", options=["", "Y", "N"], index=0)
        decision_made = st.selectbox("Decision Made (Y/N)", options=["", "Y", "N"], index=0)

    st.markdown("### Job Details")
    colA, colB = st.columns(2)
    with colA:
        company = st.text_input("Company Name", value="")
        title = st.text_input("Position Title", value="")
        location = st.text_input("Location", value="")
    with colB:
        contact_person = st.text_input("Contact Person", value="")
        contact_email = st.text_input("Contact Email", value="")
        contact_phone = st.text_input("Contact Phone", value="")

    st.markdown("### Extras")
    colX, colY = st.columns(2)
    with colX:
        next_followup = st.text_input("Next Follow-up Date", value="")
        interview_dates = st.text_input("Interview Dates", value="")
    with colY:
        resume_version = st.text_input("Resume Version Used", value="")
        cover_letter_version = st.text_input("Cover Letter Version Used", value="")

    notes = st.text_area("Notes / Observations", value="", height=100)

    st.markdown("---")
    left, right = st.columns([1,1])
    scrape_clicked = left.form_submit_button("🔎 Scrape URL to Autofill", use_container_width=True)
    save_clicked = right.form_submit_button("📤 Append to Google Sheet", use_container_width=True)

# ---------------- Actions ----------------
if scrape_clicked:
    if not url:
        st.warning("Please paste a job URL first.")
    else:
        with st.spinner("Fetching and parsing…"):
            try:
                meta = core.scrape(url)
            except Exception as e:
                st.error(f"Scrape failed: {e}")
                st.code(traceback.format_exc())
                meta = {}
        # Try to infer/auto-fill
        auto_title = meta.get("title", "")
        auto_company = meta.get("company", meta.get("__resolved_company", ""))
        auto_location = meta.get("location", "")
        # compute period from text and current time
        tzinfo = pytz.timezone(tz_name)
        now = dt.datetime.now(tzinfo)
        guessed_period = (
            core.detect_period_from_text(auto_title, url, notes) or core.guess_period(now)
        )
        # Provide hints in the UI via session_state
        st.session_state.setdefault("_scraped", {})
        st.session_state["_scraped"].update(
            {
                "company": auto_company,
                "title": auto_title,
                "location": auto_location,
                "period": guessed_period,
            }
        )
        st.success("Scrape complete. Scroll up to review and edit fields.")

# Write scraped values back into widgets if present
if "_scraped" in st.session_state and st.session_state["_scraped"]:
    s = st.session_state["_scraped"]
    # Only fill if user left it blank to avoid clobbering edits
    def _maybe_fill(key: str, value: str):
        if key in st.session_state and not st.session_state[key]:
            st.session_state[key] = value
    _maybe_fill("company", s.get("company", ""))
    _maybe_fill("title", s.get("title", ""))
    _maybe_fill("location", s.get("location", ""))
    _maybe_fill("period", s.get("period", ""))

# Append to sheet
if save_clicked:
    if not url:
        st.warning("URL is required.")
        st.stop()

    # Gather current values from session state (the form keeps them there)
    company = st.session_state.get("company", "")
    title = st.session_state.get("title", "")
    location = st.session_state.get("location", "")
    notes = st.session_state.get("Notes / Observations", notes)  # keep last value

    tzinfo = pytz.timezone(tz_name)
    date_applied = core._safe_format_now((cfg.get("defaults", {}) or {}).get("date_format") or "%Y-%m-%d %H:%M:%S", tzinfo)

    # Normalize/compute industry & period
    allowed = core.load_list(cfg, "industry_allowed")
    aliases = (cfg.get("industry_aliases") or {})
    industry_val = (
        (st.session_state.get("Industry / Sector", "") and core.normalize_industry(st.session_state.get("Industry / Sector", ""), allowed, aliases))
        or core.classify_industry(title, company, url, cfg)
        or ""
    )
    period_val = (st.session_state.get("Period", "") or core.detect_period_from_text(title, url, notes) or core.guess_period(dt.datetime.now(tzinfo)))

    aid = core.find_id_from_url(url)

    row = [
        aid,
        date_applied,
        company,
        industry_val,
        title,
        period_val,
        url,
        st.session_state.get("Contact Person", ""),
        st.session_state.get("Contact Email", ""),
        st.session_state.get("Contact Phone", ""),
        st.session_state.get("Source", (cfg.get("defaults", {}) or {}).get("source", "")),
        st.session_state.get("Application Status", (cfg.get("defaults", {}) or {}).get("status", "")),
        st.session_state.get("Next Follow-up Date", ""),
        st.session_state.get("Interview Dates", ""),
        st.session_state.get("Notes / Observations", ""),
        st.session_state.get("Resume Version Used", ""),
        st.session_state.get("Cover Letter Version Used", ""),
        st.session_state.get("Offer Received (Y/N)", ""),
        st.session_state.get("Offer Details", ""),
        st.session_state.get("Decision Made (Y/N)", ""),
    ]

    # Preview row before saving
    with st.expander("Preview row to be appended", expanded=True):
        st.write({k: v for k, v in zip(core.FIELDS, row)})

    try:
        with st.spinner("Appending to Google Sheet…"):
            ws = core.open_sheet(cfg)
            ws.append_row(row, value_input_option="USER_ENTERED")
        st.success("✔ Row appended to Google Sheet!")
    except Exception as e:
        st.error(f"Failed to append row: {e}")
        st.code(traceback.format_exc())
        st.stop()