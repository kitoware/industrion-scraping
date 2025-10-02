# PRD — Jobs Page → Google Sheet Pipeline (Firecrawl + OpenRouter LLM + Google Sheets)

## 1) Summary

Build a Python CLI script that, given one or more company “Open Positions / Careers” URLs, will:

1. Use **Firecrawl** to fetch and extract the page content.
2. Use an **OpenRouter LLM** to extract **individual job posting URLs** from that page.
3. For each job URL, use **Firecrawl** again to fetch and extract job-page content.
4. Use an **LLM** (with rules + schema) to parse and normalize fields:

   * **A:** Title
   * **B:** Company Name
   * **C:** Location Name
   * **D:** Remote OK (`"TRUE"`/`"FALSE"`)
   * **E:** Job Type (`"Full Time"|"Part Time"|"Internship"`)
   * **F:** Description (HTML from Firecrawl)
   * **G:** Minimum Salary (empty if not present)
   * **H:** Maximum Salary (empty if not present)
   * **I:** Application Link
5. **Append** rows into a **Google Sheet** in that exact column order.

The script emphasizes idempotency, deduplication, and resilience against pagination, JS-heavy sites, and inconsistent job schemas.

---

## 2) Goals & Non-Goals

### Goals

* End-to-end automated pipeline from a careers URL to a structured Google Sheet.
* High recall of job URLs on common ATS pages (Greenhouse, Lever, Workday, Ashby, etc.) and custom careers pages.
* Accurate, schema-conformant extraction with **no human-in-the-loop**.
* Configurable & repeatable CLI; safe to run daily or ad hoc.
* Clear logs, metrics, and robust error handling.

### Non-Goals

* No ATS authentication, resume upload, or candidate management.
* No multi-language translation; assume the page is in English (v1).
* No currency normalization; salaries are parsed as displayed (v1).
* No browser automation (e.g., Playwright) in v1—rely on Firecrawl’s rendering (per your docs).

---

## 3) Assumptions

* Firecrawl is available via Python SDK or HTTP and documented in `docs/firecrawl-scraping.md` (you’ll include).
* OpenRouter API key is available and approved for the chosen model(s).
* A Google Service Account has edit access to the target Google Sheet.
* Target careers pages publicly expose job listings/links (no login).
* Output Google Sheet exists (or script can create it if configured).

---

## 4) Users & Stories

* **Ops/Research Analyst**: “Give me an updated list of jobs from Company X’s careers page in a sheet.”
* **Founder/PM**: “Track jobs at target partners weekly; keep the sheet fresh without duplicates.”
* **Growth Analyst**: “Filter for ‘Remote OK’ and ‘Internship’ across multiple companies.”

---

## 5) Success Metrics

* **Extraction success rate**: ≥ 95% of *valid* job links parsed into the sheet.
* **Schema conformance**: ≥ 99% rows with all required fields filled or intentionally empty (G/H).
* **Duplicate rate**: ≤ 1% duplicates across runs (per canonical URL + title hash).
* **Runtime**: ≤ 2 minutes per 100 jobs (assuming moderate network & model latency).
* **Cost**: ≤ \$0.75 per 100 job pages on default model (estimate; see §18).

---

## 6) High-Level Architecture

```
[Input URLs] 
    │
    ▼
[Firecrawl: Careers Page] ──► [LLM: Extract Job URLs] ──► [Unique Job URL Set]
                                                │
                                for each job URL │
                                                ▼
                              [Firecrawl: Job Page Content (HTML + text)]
                                                │
                                                ▼
                               [LLM: Extract Fields per JSON Schema]
                                                │
                         [Heuristics & Validation, Dedup & Cache]
                                                │
                                                ▼
                                       [Google Sheets Append]
```

---

## 7) Detailed Workflow

1. **Input ingestion**

   * Accept one careers URL or a file (`--input urls.txt`) listing multiple URLs.
   * Optional `--company "Company Name"` overrides company name if missing on page.

2. **Careers-page scrape (Firecrawl)**

   * Use “article + links” mode (as available) to capture HTML and anchor tags.
   * Normalize base URL; collect visible anchors; keep raw HTML for LLM context.

3. **Job-URL extraction (LLM)**

   * Prompt the OpenRouter LLM with: site origin, extracted anchors (URL + anchor text), and short page summary.
   * Instruct model to **return only job posting URLs** as a JSON array, removing duplicates, ensuring absolute URLs.

4. **Job-page scrape (Firecrawl)**

   * For each unique job URL, request HTML + main content.
   * Capture canonical URL (`<link rel="canonical">` if present) and all “Apply” anchors.

5. **Field extraction (LLM + guardrails)**

   * Provide the LLM a **strict JSON schema**.
   * Supply raw HTML (truncated/chunked with a short, model-aware context strategy).
   * Ask for: Title, Company Name, Location Name, Remote OK, Job Type, Description (HTML), Min Salary, Max Salary, Application Link.
   * **Heuristics/Fallbacks** (post-LLM):

     * **Remote OK**: If LLM returns `null/unknown`, apply regex over text for `remote|work from anywhere|WFH|hybrid` → TRUE if explicit remote; FALSE otherwise.
     * **Job Type**: Map synonyms: `full-time|permanent` → “Full Time”; `part-time` → “Part Time”; `intern|co-op` → “Internship”.
     * **Salary**: If none found, leave G/H empty. If found hourly, still store numeric min/max from the page text, do not normalize periodicity (v1).
     * **Application Link**: Prefer an “Apply”/“Submit” anchor that leads off-page/ATS; fallback to the job URL.

6. **Validation & Deduplication**

   * Compute a row fingerprint: `sha256(canonical_url || title || company)`.
   * Maintain a local cache (SQLite/JSON) and optionally read existing Sheet values to skip duplicates.
   * Ensure `Remote OK` is **exactly** `"TRUE"` or `"FALSE"` as strings.

7. **Write to Google Sheet**

   * Ensure header row exists in order A–I.
   * Append in batches (≤ 500 rows per batch).
   * Optional formatting (wrap text for Description column; freeze header row).

8. **Output & Metrics**

   * Print a summary: careers pages processed, job URLs found, rows appended, duplicates skipped, errors.
   * Emit JSON log line for machine parsing (counts, timings, model name, token usage if available).

---

## 8) Data Contracts

### 8.1 LLM Output Schema — Job URLs

```json
{
  "type": "object",
  "properties": {
    "jobs": {
      "type": "array",
      "items": { "type": "string", "format": "uri" }
    },
    "notes": { "type": "string" }
  },
  "required": ["jobs"]
}
```

### 8.2 LLM Output Schema — Job Fields

```json
{
  "type": "object",
  "properties": {
    "title":        { "type": "string", "minLength": 1 },
    "company_name": { "type": "string", "minLength": 1 },
    "location":     { "type": "string" },
    "remote_ok":    { "type": "boolean" },
    "job_type":     { "type": "string", "enum": ["Full Time", "Part Time", "Internship"] },
    "description_html": { "type": "string" },
    "min_salary":   { "type": ["number","null"] },
    "max_salary":   { "type": ["number","null"] },
    "application_link": { "type": "string", "format": "uri" }
  },
  "required": ["title","company_name","remote_ok","job_type","description_html","application_link"]
}
```

### 8.3 Google Sheet Column Ordering

* **A:** `title`
* **B:** `company_name`
* **C:** `location`
* **D:** `remote_ok` → **string** `"TRUE"`/`"FALSE"`
* **E:** `job_type`
* **F:** `description_html`
* **G:** `min_salary` (empty if null)
* **H:** `max_salary` (empty if null)
* **I:** `application_link`

---

## 9) Prompt Design (key excerpts)

### 9.1 Careers Page → Job URLs

**System:**
“You extract job posting URLs from a careers page’s anchors. Return a JSON object matching the schema. Only return individual job posting URLs, not category or filter pages. Use absolute URLs.”

**User (variables in `<>`):**

* **Origin:** `<base_url>`
* **Anchors (top N by relevance):**

  ```
  [
    {"href": "...", "text":"..."},
    ...
  ]
  ```
* “Return only job posting URLs. Avoid category/filters/pagination. Deduplicate.”

### 9.2 Job Page → Fields

**System:**
“You are an expert ATS parser. Extract fields for the provided JSON schema. Follow these rules strictly:

* Prefer exact strings from the page for title and location.
* `remote_ok` must be boolean; infer only if clearly stated.
* `job_type` must be one of: Full Time, Part Time, Internship.
* `description_html` must be HTML of the job description (not full page).
* If salary not present, set both salaries to null.
* `application_link` should be the primary apply URL; fall back to the job page URL if none.”

**User:**

* **Job URL:** `<job_url>`
* **Canonical URL (if any):** `<canonical>`
* **HTML (truncated/chunked, ensure relevant nodes provided):**

  ```
  <div id="job-description"> ... </div>
  ```
* **Notes:** “Common signals: ‘Apply’, ‘Responsibilities’, ‘Qualifications’. Words like ‘Remote’/‘Hybrid’ may influence `remote_ok`.”

---

## 10) Configuration

`config/config.yaml`

```yaml
openrouter:
  api_key_env: OPENROUTER_API_KEY
  model_job_links: "openrouter/llama-3.1-70b-instruct"   # example; configurable
  model_job_fields: "openrouter/claude-3.5-sonnet"        # example; configurable
  max_tokens: 2000
  temperature: 0.2
  timeout_seconds: 60

firecrawl:
  api_key_env: FIRECRAWL_API_KEY
  render_js: true
  extract_links: true
  request_timeout: 30

google_sheets:
  service_account_json_env: GOOGLE_APPLICATION_CREDENTIALS  # path to JSON
  spreadsheet_id: "<YOUR_SHEET_ID>"
  worksheet_name: "Jobs"
  batch_size: 200

runtime:
  input_file: null      # e.g., "urls.txt"
  single_url: null      # e.g., "https://company.com/careers"
  company_override: null
  concurrency: 8
  retry:
    max_attempts: 3
    backoff_seconds: 2
  cache_path: "data/cache.sqlite"
  log_level: "INFO"
```

Environment variables:

* `OPENROUTER_API_KEY`
* `FIRECRAWL_API_KEY` (if required)
* `GOOGLE_APPLICATION_CREDENTIALS` (path to service account JSON)

---

## 11) CLI

```
python jobs_pipeline.py \
  --sheet-id <SPREADSHEET_ID> \
  --worksheet "Jobs" \
  --url "https://company.com/careers" \
  --company "Company, Inc." \
  --config config/config.yaml
```

Or via file:

```
python jobs_pipeline.py --input urls.txt --sheet-id <ID>
```

Options:

* `--dry-run` (skip Google write; output CSV locally)
* `--resume` (use cache to skip processed URLs)
* `--concurrency N`
* `--max-jobs N` (cap for testing)

---

## 12) Data Persistence & Idempotency

* **Cache (SQLite)** tables:

  * `careers_pages(url TEXT PRIMARY KEY, last_fetched_at TIMESTAMP, status TEXT)`
  * `jobs(url TEXT PRIMARY KEY, canonical_url TEXT, title TEXT, company TEXT, fingerprint TEXT, first_seen TIMESTAMP)`
* **Fingerprint**: `sha256(canonical_url||title||company_name)`
* On each run, skip rows whose fingerprint already exists in Sheet or cache.

---

## 13) Error Handling & Retries

* Network/HTTP failures: retry with exponential backoff (`2^n` seconds, jitter).
* Firecrawl timeouts: 2 retries, then mark failed.
* LLM schema violations: 2 retries with stricter instructions; on final failure, log and skip.
* Google Sheets rate errors: backoff and retry batch.

---

## 14) Logging & Observability

* **Structured JSON logs** to stdout: event, level, url, duration\_ms, counts.
* Summary at end: `{"careers_processed":X,"job_urls_found":Y,"rows_appended":Z,"duplicates":D,"errors":E}`
* Optional: write a `metrics.json` artifact with counters and timestamps.

---

## 15) Performance & Cost Considerations

* **Batch careers pages** and **concurrent job fetches** (bounded with `--concurrency`).
* **Chunk job HTML** for LLM to reduce tokens:

  * Extract only relevant nodes (description container, job header, apply section).
  * Keep raw HTML for Description field but minimize surrounding boilerplate.
* Prefer efficient models on OpenRouter for URL extraction and a stronger model for field parsing.
* Cache results; skip unchanged jobs.

**Rough cost (illustrative only):**

* Job-links prompt: small context, cheap.
* Job-fields prompt: moderate context.
* At 100 jobs: ballpark **\$0.25–\$0.75** with efficient models. (Adjust per actual model pricing.)

---

## 16) Security, Compliance, & Legal

* Respect site **robots.txt** and terms; you decide policy (config flag).
* Set a descriptive **User-Agent** with contact email.
* Store only **public** info; no PII beyond job postings.
* Securely handle API keys and service account JSON (env vars / secret manager).
* Provide `--rate-limit` options to be good citizens.

---

## 17) Google Sheets Integration Details

* Use **gspread** or the official `google-api-python-client`.
* Ensure the **service account** email has edit access to the Sheet.
* On first write:

  * Create header row `["Title","Company Name","Location Name","Remote OK","Job Type","Description","Minimum Salary","Maximum Salary","Application Link"]`
  * Freeze row 1.
* Append rows in **batches**; wrap text enabled for Description column for readability (optional).

---

## 18) Testing Plan

### Unit Tests

* **URL extractor** prompt → validate it filters category/filter/pagination links.
* **Field parser** prompt → validate enum mapping, boolean casting, empty salaries when missing.
* **Heuristics**: remote detection, job type mapping, salary regex fallback.

### Integration Tests

* Mock Firecrawl responses for: Greenhouse, Lever, Workday, custom HTML.
* Mock LLM outputs (golden JSON fixtures).
* Validate Google Sheets batch append is correct and ordered A–I.

### End-to-End

* Use a known public careers page with a handful of roles.
* Verify deduping across two consecutive runs.
* Dry-run CSV matches the appended rows.

---

## 19) Risks & Mitigations

* **Dynamic content / heavy JS** → Firecrawl `render_js: true`.
* **Model hallucination** → Strict schema, validation, and retries; minimal context; post-parse heuristics.
* **Frequent site layout changes** → Rely on general structure (headings, apply anchors), not brittle CSS selectors.
* **Rate limits** → Configurable concurrency, backoff, and optional per-domain throttling.

---

## 20) Future Extensions

* Pagination auto-discovery on careers pages.
* Currency & periodicity normalization (e.g., hourly/monthly/annual).
* Additional fields (department, requisition ID, posting date).
* Multi-language support.
* Export to CSV/Parquet/DB in addition to Sheets.
* Slack/Email summary after run.

---

## 21) Project Structure

```
.
├─ jobs_pipeline.py
├─ config/
│  └─ config.yaml
├─ docs/
│  └─ firecrawl-scraping.md              # (you will add)
├─ prompts/
│  ├─ job_urls_extractor.md
│  └─ job_fields_extractor.md
├─ schemas/
│  ├─ job_urls.schema.json
│  └─ job_fields.schema.json
├─ data/
│  └─ cache.sqlite
├─ tests/
│  ├─ test_url_extraction.py
│  ├─ test_field_extraction.py
│  └─ fixtures/
└─ utils/
   ├─ firecrawl_client.py
   ├─ llm_client.py
   ├─ sheets_client.py
   ├─ parsing.py
   ├─ cache.py
   └─ logging.py
```

---

## 22) Implementation Plan (Step-by-Step)

**Step 0 — Setup**

* Create repo structure above; add `config/config.yaml` template.
* Wire env vars; install deps: `gspread`, `google-auth`, `pydantic`, `requests`/`httpx`, `tenacity` (retry), `orjson`, `pandas` (optional), `python-dotenv` (optional).

**Step 1 — Firecrawl client**

* Thin wrapper with options from config; funcs: `fetch_page(url) -> {html, text, links, canonical}`.

**Step 2 — LLM client (OpenRouter)**

* Generic `complete_json(prompt, schema) -> dict` with strict parsing & retry on invalid JSON.
* Support model selection per use case.

**Step 3 — URL Extraction**

* Build prompt (#9.1), pass anchors (href + text), base URL, and page snippet; validate output vs `job_urls.schema.json`; absolutize and dedupe.

**Step 4 — Job Page Extraction**

* For each job URL: Firecrawl fetch; capture canonical, main content HTML.
* Extract likely description container (heuristic: look for `<section role="main">`, `#content`, `.job`, `.description` etc.), but keep **full HTML** for the LLM to decide and to store in F.

**Step 5 — Field Parsing**

* Prompt (#9.2) with HTML. Validate JSON vs `job_fields.schema.json`.
* Post-process:

  * `remote_ok`: cast to `"TRUE"`/`"FALSE"`.
  * `job_type`: normalize synonyms → enum.
  * `min_salary`/`max_salary`: keep empty strings if null.
  * `application_link`: prefer ATS “Apply” anchors; else the job URL.

**Step 6 — Dedup & Cache**

* Compute fingerprint; skip if fingerprint seen (cache or Sheet).
* Upsert cache.

**Step 7 — Google Sheets Write**

* Ensure header; batch-append A–I in order.
* Optional: apply wrap text and freeze header.

**Step 8 — CLI & Orchestration**

* argparse for flags; support `--dry-run`, `--resume`, `--concurrency`.
* Parallelize job-page steps with `asyncio` or `concurrent.futures` (bounded).

**Step 9 — Tests & Fixtures**

* Unit tests for parsing & mapping; integration with mocked clients.

**Step 10 — Docs**

* Usage README; update `docs/firecrawl-scraping.md` pointer; list models supported and any quirks.

---

## 23) Pseudocode (Reference)

```python
def run_pipeline(config, input_urls, sheet_id, worksheet):
    careers_urls = resolve_input(input_urls)

    for careers_url in careers_urls:
        page = firecrawl.fetch_page(careers_url)
        anchors = extract_anchors(page)
        job_urls_obj = llm.extract_job_urls(base=careers_url, anchors=anchors)
        job_urls = dedupe_absolutize(job_urls_obj.jobs, careers_url)

        for job_url in bounded_concurrency(job_urls, n=config.runtime.concurrency):
            if cache.is_seen(job_url): 
                continue

            job_page = firecrawl.fetch_page(job_url)
            html = job_page.html
            canonical = job_page.canonical or job_url

            fields = llm.extract_job_fields(job_url, canonical, html)
            fields = postprocess_fields(fields, company_override=config.runtime.company_override)

            row = to_sheet_row(fields)
            fp  = fingerprint(fields, canonical)
            if not cache.is_fingerprint_seen(fp) and not sheet.row_exists(fp):
                sheet.append_row(row)
                cache.mark(fp, job_url)
```

`to_sheet_row(fields)` → `[
  fields.title,
  fields.company_name,
  fields.location or "",
  "TRUE" if fields.remote_ok else "FALSE",
  fields.job_type,
  fields.description_html,
  "" if fields.min_salary is None else fields.min_salary,
  "" if fields.max_salary is None else fields.max_salary,
  fields.application_link
]`

---

## 24) Acceptance Criteria

* Running the CLI with a careers URL produces rows in the target Google Sheet with the correct header and column order.
* “Remote OK” is always `"TRUE"` or `"FALSE"` (strings).
* If salary not in description, **G/H are empty**.
* Job Type is exactly one of the three enumerations.
* Description column contains **HTML** from the page (not plain text).
* Duplicate jobs are not inserted on subsequent runs.
* Logs show counts and errors; exit code 0 on success.

---

If you want, I can also drop in ready-to-use **prompt files**, a minimal **Pydantic model** for schema validation, and a **requirements.txt** scaffold next.
