# Streamlit GUI for JobLinkBot (diagnostic build)
# ------------------------------------------------------------
# This version adds loud diagnostics so a blank page is easier to debug.
# - Always renders visible markers (even if imports/config fail)
# - Sidebar "Debug" shows cwd, files, sys.path, and any exceptions
# - Also prints to server console (where you launched `streamlit run app.py`)

import os
import sys
import platform
import traceback
import datetime as dt
from typing import Dict
import pytz

import streamlit as st

# --- Early page paint ---
st.set_page_config(page_title="JobLinkBot GUI", page_icon="🗂️", layout="centered")
st.write("### ✅ Page loaded (marker A)")
st.caption("If you only see this line and nothing else, tell me. The rest of the script didn't run.")

# Also log to console
print("[JobLinkBot] Streamlit app starting…")
print(f"[JobLinkBot] Python: {sys.version}")
print(f"[JobLinkBot] Platform: {platform.platform()}")
print(f"[JobLinkBot] CWD: {os.getcwd()}")

# ---------------- Try importing core module ----------------
core = None
core_import_tb = ""
try:
    import joblinkbot_refactor as core
    print("[JobLinkBot] Imported joblinkbot_refactor successfully.")
except Exception as e:
    core_import_tb = traceback.format_exc()
    print("[JobLinkBot] ERROR importing joblinkbot_refactor:", core_import_tb)

# ---------------- Sidebar Debug Pane ----------------
with st.sidebar:
    st.title("🛠️ Debug")
    st.write("This panel helps diagnose blank screens.")
    st.markdown("**CWD**: `" + os.getcwd() + "`")
    try:
        files = os.listdir(".")
    except Exception as e:
        files = [f"<listdir error: {e}>"]
    st.markdown("**Files in folder:**")
    st.code("".join(str(x) for x in files))

    st.markdown("**sys.path (first 10):**")
    st.code("".join(sys.path[:10]))

    st.markdown("**Environment**")
    st.code(
        f"Python: {sys.version.splitlines()[0]} Platform: {platform.platform()} Streamlit: {st.__version__}"
    )

    if core_import_tb:
        st.error("Import error: joblinkbot_refactor")
        st.code(core_import_tb)

# ---------------- Load config (safe) ----------------
cfg: Dict = {}
load_cfg_tb = ""
if core:
    try:
        @st.cache_resource(show_spinner=False)
        def _load_cfg() -> Dict:
            return core.load_config()
        cfg = _load_cfg()
        print("[JobLinkBot] Loaded config.yml successfully.")
    except Exception:
        load_cfg_tb = traceback.format_exc()
        print("[JobLinkBot] ERROR loading config.yml:", load_cfg_tb)

if load_cfg_tb:
    st.warning("Failed to load config.yml — you can still test the UI in Demo mode.")

# ---------------- Demo toggle ----------------
with st.sidebar:
    demo_mode = st.toggle(
        "Demo mode (no Google Sheets)",
        value=bool(load_cfg_tb or not core),
        help="Use this to test the UI without credentials or the core module.",
    )

# ---------------- Main UI (guarded) ----------------
st.write("### 🗂️ JobLinkBot — Add Job to Sheet (marker B)")

try:
    tz_name = (cfg.get("defaults", {}) or {}).get("timezone", "America/New_York") if core else "America/New_York"
    allowed_industries = [str(x) for x in (cfg.get("industry_allowed") or [])] if cfg else ["Finance","Biotech","Tech","Consulting","Manufacturing","Healthcare"]

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

    # After-form markers
    st.info("Markers: you should see A (top), B (above form), and this info box. If not, the script stopped early.")

    # Scrape
    if scrape_clicked:
        if not url:
            st.warning("Please paste a job URL first.")
        elif not core:
            st.error("Scrape requires joblinkbot_refactor.py next to app.py.")
        else:
            try:
                st.write("Scraping… (marker C)")
                meta = core.scrape(url)
                st.success("Scrape complete.")
            except Exception:
                st.error("Scrape failed.")
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
            st.experimental_rerun()

    if "_scraped" in st.session_state:
        st.write("Loaded scraped fields:")
        st.code(st.session_state["_scraped"])    

    # Save
    if save_clicked:
        if demo_mode or not (core and cfg):
            st.info("Demo mode or missing config — preview only.")
        else:
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
                    st.write(row)
                ws = core.open_sheet(cfg)
                ws.append_row(row, value_input_option="USER_ENTERED")
                st.success("✔ Row appended to Google Sheet!")
            except Exception:
                st.error("Append failed.")
                st.code(traceback.format_exc())

except Exception:
    # Catch absolutely everything and print both places
    tb = traceback.format_exc()
    print("[JobLinkBot] FATAL error in app body:", tb)
    st.error("Fatal error while building the page.")
    st.code(tb)
    st.stop()