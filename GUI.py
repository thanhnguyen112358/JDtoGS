# Streamlit GUI for JobLinkBot (robust version)
# ------------------------------------------------------------
# This app wraps the logic in joblinkbot_refactor.py with a friendly UI.
# Put this file in the same directory as joblinkbot_refactor.py and config.yml
# then run:
#   pip install -r requirements.txt
#   streamlit run app.py
# ------------------------------------------------------------
# Notes:
# - This version is defensive: it always renders a visible page even if imports/config fail.
# - "Demo mode" lets you test the UI without Google Sheets.

import streamlit as st
import datetime as dt
import pytz
import traceback
from typing import Dict

st.set_page_config(page_title="JobLinkBot GUI", page_icon="🗂️", layout="centered")

st.title("🗂️ JobLinkBot — Add Job to Sheet")
st.caption("Paste a posting URL, auto-fill details, tweak, then save to Google Sheets.")

# ---------------- Import core module (safe) ----------------
core = None
core_import_error = None
try:
    import joblinkbot_refactor as core
except Exception as e:
    core_import_error = e

# ---------------- Sidebar: Config & Help ----------------
with st.sidebar:
    st.title("⚙️ Settings")
    if core_import_error:
        st.error("Couldn't import joblinkbot_refactor.py. Make sure it's in the same folder.")
        st.code(traceback.format_exc())
    st.markdown(
        "This GUI uses **config.yml** next to the script. Update your Google Sheet ID, worksheet name, and service account JSON path there."
    )
    reload_clicked = st.button("Reload config.yml cache")
    demo_mode = st.toggle("Demo mode (no Google Sheets)", value=False, help="Use this to test the UI even if config/creds are missing.")

# ---------------- Load config (safe) ----------------
cfg: Dict = {}
load_cfg_error = None
if core and not demo_mode:
    try:
        if reload_clicked:
            st.cache_data.clear()
            st.cache_resource.clear()
        @st.cache_resource(show_spinner=False)
        def _load_cfg_cached() -> Dict:
            return core.load_config()
        cfg = _load_cfg_cached()
    except Exception as e:
        load_cfg_error = e

# Inform user but DO NOT stop the app (still render UI)
if load_cfg_error and not demo_mode:
    st.warning(f"Failed to load config.yml: {load_cfg_error}")

# Prepare basics from config or sensible defaults
if core:
    tz_name = (cfg.get("defaults", {}) or {}).get("timezone", "America/New_York")
    allowed_industries = [str(x) for x in (cfg.get("industry_allowed") or [])]
else:
    tz_name = "America/New_York"
    allowed_industries = ["Finance","Biotech","Tech","Consulting","Manufacturing","Healthcare"]

# ---------------- Main: Form ----------------
with st.form("job_form", clear_on_submit=False):
    url = st.text_input("Application Link (URL)", placeholder="https://boards.greenhouse.io/... or https://...myworkdayjobs.com/...", help="Paste the full job posting URL.")

    col1, col2 = st.columns(2)
    with col1:
        default_source = (cfg.get("defaults", {}) or {}).get("source", "") if cfg else "Job Board"
        default_status = (cfg.get("defaults", {}) or {}).get("status", "") if cfg else "Saved"
        source = st.text_input("Source", value=default_source, key="Source")
        status = st.text_input("Application Status", value=default_status, key="Application Status")
        industry = st.selectbox("Industry / Sector", options=[""] + allowed_industries, index=0, key="Industry / Sector")
    with col2:
        period = st.selectbox("Period", options=["", "Spring", "Summer", "Fall"], index=0, key="Period")
        offer_received = st.selectbox("Offer Received (Y/N)", options=["", "Y", "N"], index=0, key="Offer Received (Y/N)")
        decision_made = st.selectbox("Decision Made (Y/N)", options=["", "Y", "N"], index=0, key="Decision Made (Y/N)")

    st.markdown("### Job Details")
    colA, colB = st.columns(2)
    with colA:
        company = st.text_input("Company Name", value="", key="Company Name")
        title = st.text_input("Position Title", value="", key="Position Title")
        location = st.text_input("Location", value="", key="Location")
    with colB:
        contact_person = st.text_input("Contact Person", value="", key="Contact Person")
        contact_email = st.text_input("Contact Email", value="", key="Contact Email")
        contact_phone = st.text_input("Contact Phone", value="", key="Contact Phone")

    st.markdown("### Extras")
    colX, colY = st.columns(2)
    with colX:
        next_followup = st.text_input("Next Follow-up Date", value="", key="Next Follow-up Date")
        interview_dates = st.text_input("Interview Dates", value="", key="Interview Dates")
    with colY:
        resume_version = st.text_input("Resume Version Used", value="", key="Resume Version Used")
        cover_letter_version = st.text_input("Cover Letter Version Used", value="", key="Cover Letter Version Used")

    notes = st.text_area("Notes / Observations", value="", height=100, key="Notes / Observations")

    st.markdown("---")
    left, right = st.columns([1,1])
    scrape_clicked = left.form_submit_button("🔎 Scrape URL to Autofill", use_container_width=True)
    save_clicked = right.form_submit_button("📤 Append to Google Sheet", use_container_width=True)

# ---------------- Actions: Scrape ----------------
if scrape_clicked:
    if not url:
        st.warning("Please paste a job URL first.")
    elif not core:
        st.error("Scrape requires joblinkbot_refactor.py. Place it next to app.py.")
    else:
        with st.spinner("Fetching and parsing…"):
            try:
                meta = core.scrape(url)
            except Exception as e:
                st.error(f"Scrape failed: {e}")
                st.code(traceback.format_exc())
                meta = {}
        auto_title = meta.get("title", "")
        auto_company = meta.get("company", meta.get("__resolved_company", ""))
        auto_location = meta.get("location", "")
        tzinfo = pytz.timezone(tz_name)
        guessed_period = (
            core.detect_period_from_text(auto_title, url, notes) if core else ""
        ) or (core.guess_period(dt.datetime.now(tzinfo)) if core else "")
        st.session_state.setdefault("_scraped", {})
        st.session_state["_scraped"].update({
            "Company Name": auto_company,
            "Position Title": auto_title,
            "Location": auto_location,
            "Period": guessed_period,
        })
        st.success("Scrape complete. Scroll up to review and edit fields.")

# Write scraped values back into widgets if present
if "_scraped" in st.session_state and st.session_state["_scraped"]:
    for k, v in st.session_state["_scraped"].items():
        if k in st.session_state and not st.session_state[k]:
            st.session_state[k] = v

# ---------------- Actions: Save ----------------
if save_clicked:
    if not url:
        st.warning("URL is required.")
    elif demo_mode or not (core and cfg):
        # Demo: preview only
        st.info("Demo mode or missing config: preview only, nothing will be written to Google Sheets.")
        if core:
            tzinfo = pytz.timezone(tz_name)
            date_applied = core._safe_format_now((cfg.get("defaults", {}) or {}).get("date_format") or "%Y-%m-%d %H:%M:%S", tzinfo) if cfg else dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            aid = core.find_id_from_url(url)
            row = [
                aid,
                date_applied,
                st.session_state.get("Company Name", ""),
                st.session_state.get("Industry / Sector", ""),
                st.session_state.get("Position Title", ""),
                st.session_state.get("Period", ""),
                url,
                st.session_state.get("Contact Person", ""),
                st.session_state.get("Contact Email", ""),
                st.session_state.get("Contact Phone", ""),
                st.session_state.get("Source", ""),
                st.session_state.get("Application Status", ""),
                st.session_state.get("Next Follow-up Date", ""),
                st.session_state.get("Interview Dates", ""),
                st.session_state.get("Notes / Observations", ""),
                st.session_state.get("Resume Version Used", ""),
                st.session_state.get("Cover Letter Version Used", ""),
                st.session_state.get("Offer Received (Y/N)", ""),
                st.session_state.get("Offer Details", ""),
                st.session_state.get("Decision Made (Y/N)", ""),
            ]
            with st.expander("Preview row (demo mode)", expanded=True):
                st.write({k: v for k, v in zip(core.FIELDS, row)})
        else:
            st.warning("Core module missing; cannot build preview.")
    else:
        # Real append path
        try:
            tzinfo = pytz.timezone(tz_name)
            date_applied = core._safe_format_now((cfg.get("defaults", {}) or {}).get("date_format") or "%Y-%m-%d %H:%M:%S", tzinfo)
            allowed = core.load_list(cfg, "industry_allowed")
            aliases = (cfg.get("industry_aliases") or {})
            title = st.session_state.get("Position Title", "")
            company = st.session_state.get("Company Name", "")
            notes = st.session_state.get("Notes / Observations", "")
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
            with st.expander("Preview row to be appended", expanded=True):
                st.write({k: v for k, v in zip(core.FIELDS, row)})
            with st.spinner("Appending to Google Sheet…"):
                ws = core.open_sheet(cfg)
                ws.append_row(row, value_input_option="USER_ENTERED")
            st.success("✔ Row appended to Google Sheet!")
        except Exception as e:
            st.error(f"Failed to append row: {e}")
            st.code(traceback.format_exc())