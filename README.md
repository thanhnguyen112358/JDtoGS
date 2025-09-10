# Job Link → Google Sheet (Starter)

**Paste a job posting URL**, and this tool will try to **parse key fields** and **append a row** to your Google Sheet.

### What it captures
- **Application ID** (best-effort: from URL or page)
- **Date Applied** (the moment you add it)
- **Company**, **Location**, **Position Title**
- **Industry/Sector** (mapped via `industry_map.yml` keys or CLI flag)
- **Period** (Spring/Summer/Fall) — guessed from date unless overridden
- **Application Link**, **Source** (LinkedIn, Career Fair, etc.)
- **Application Status** (Applied/Submitted/Interviewing/etc.)

### Quick Start
1. **Python env**
   ```bash
   python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   ```

2. **Google Sheets auth (service account)**
   - Go to Google Cloud → create a **Service Account** and create a **JSON key** file.
   - Put the JSON in this folder, e.g. `service_account.json`.
   - Share your target Google Sheet with the service account email (Editor permission).
   - Copy the **Sheet ID** from the sheet URL (between `/d/` and `/edit`).

3. **Configure**
   - Edit `config.yml`:
     ```yaml
     google:
       sheet_id: "YOUR_SHEET_ID"
       service_account_json: "service_account.json"
     defaults:
       timezone: "America/New_York"
       source: "LinkedIn"
       status: "Applied"
     ```
   - Optionally edit `industry_map.yml` to map keywords to industries.

4. **Prepare the Sheet**
   - Create a header row (A1 row) in the first worksheet with:
     ```
     Application ID,Date Applied,Company,Location,Industry,Position Title,Period,Application Link,Source,Application Status,Notes,Domain,Raw Title,Raw Company,Raw Location
     ```

5. **Use it**
   ```bash
   python app.py add "https://boards.greenhouse.io/example/jobs/1234567" --source LinkedIn --status Applied
   python app.py add "https://jobs.lever.co/foo/abcd-1234" --industry Energy
   ```

6. **Notes**
   - Supported adapters (best-effort): **Greenhouse, Lever, Workday, LinkedIn** + a generic HTML/OpenGraph fallback.
   - You can override any field at the CLI: `--company`, `--title`, `--location`, `--industry`, `--period`, `--status`, `--source`, `--notes`.
   - Period guess: Spring = Jan–Apr, Summer = May–Aug, Fall = Sep–Dec (configurable in code).

---

_Starter generated on 2025-09-09._
