import argparse
import concurrent.futures
import csv
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import yaml
from dotenv import load_dotenv

from utils.logging import get_logger, log_event
from utils.firecrawl_client import FirecrawlClient
from utils.llm_client import OpenRouterClient
# from utils.sheets_client import SheetsClient
from utils.parsing import (
    absolutize_and_dedupe_urls,
    extract_anchors_from_page_data,
    postprocess_fields,
    to_sheet_row,
)
from utils.ats.bamboohr import BambooHRParser
from utils.cache import Cache


def load_config(config_path: Optional[str]) -> Dict[str, Any]:
    default_path = Path("config/config.yaml")
    path = Path(config_path) if config_path else default_path
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def resolve_input(single_url: Optional[str], input_file: Optional[str]) -> List[str]:
    urls: List[str] = []
    if single_url:
        urls.append(single_url.strip())
    if input_file:
        with open(input_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    urls.append(line)
    # Deduplicate while preserving order
    seen = set()
    result = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            result.append(u)
    return result


def fingerprint(canonical_url: str, title: str, company: str) -> str:
    concat = f"{canonical_url}||{title}||{company}"
    return hashlib.sha256(concat.encode("utf-8")).hexdigest()


def run_pipeline(
    config: Dict[str, Any],
    careers_urls: List[str],
    sheet_id: Optional[str],
    worksheet: Optional[str],
    company_override: Optional[str],
    dry_run: bool,
    resume: bool,
    concurrency: int,
    max_jobs: Optional[int],
    emit_stdout: bool = True,
    collect_errors: bool = True,
) -> Dict[str, Any]:
    logger = get_logger()
    runtime_cfg = config.get("runtime", {})
    cache_path = runtime_cfg.get("cache_path", "data/cache.sqlite")
    Path(cache_path).parent.mkdir(parents=True, exist_ok=True)
    cache = Cache(cache_path)

    firecrawl = FirecrawlClient(config.get("firecrawl", {}))
    llm = OpenRouterClient(config.get("openrouter", {}))
    bamboohr_parser = BambooHRParser(timeout=float(runtime_cfg.get("bamboohr_timeout", 20.0)))

    sheets: Optional["SheetsClient"] = None
    if not dry_run:
        if not sheet_id:
            print("--sheet-id is required unless --dry-run is set", file=sys.stderr)
            sys.exit(2)
        # Lazy import to avoid requiring gspread during dry runs
        from utils.sheets_client import SheetsClient
        sheets = SheetsClient(
            spreadsheet_id=sheet_id,
            worksheet_name=worksheet or config.get("google_sheets", {}).get("worksheet_name", "Jobs"),
            cfg=config.get("google_sheets", {}),
        )
        sheets.ensure_header()

    totals = {
        "careers_processed": 0,
        "job_urls_found": 0,
        "rows_appended": 0,
        "duplicates": 0,
        "errors": 0,
    }
    errors: List[Dict[str, Any]] = []

    for careers_url in careers_urls:
        try:
            page_data = firecrawl.fetch_page(careers_url)
            anchors = extract_anchors_from_page_data(page_data)
            limited_anchors = anchors[:150]

            # Ask LLM for indices of anchors that represent job postings to avoid long JSON outputs
            user_prompt = {
                "Origin": careers_url,
                "Anchors": limited_anchors,
                "Instruction": (
                    "Select ONLY the indices of anchors that are individual job postings. "
                    "Be VERY selective - choose maximum 30 indices. "
                    "Exclude category, team, filter, search, pagination, and general navigation links. "
                    "Focus on links that clearly lead to specific job descriptions."
                ),
            }
            indices_schema = llm.load_schema(Path("schemas/job_urls_indices.schema.json"))
            indices_schema_text = json.dumps(indices_schema, separators=(",", ":"))
            # Embed the JSON Schema text directly in the system prompt to ensure the model sees it
            sel_obj = llm.complete_json(
                system_prompt=(
                    "You are a precise job posting selector. Given a list of anchors with href and text, "
                    f"return ONLY a JSON object that conforms to this JSON Schema (Draft 2020-12):\n{indices_schema_text}\n"
                    "CRITICAL RULES:\n"
                    "- Select MAXIMUM 30 indices (preferably 10-20)\n"
                    "- Only choose anchors that are clearly individual job postings\n"
                    "- Exclude category/team/filter/search/pagination links\n"
                    "- Be conservative - when in doubt, exclude it\n"
                    "- Output must be valid JSON with 'indices' array containing integers only"
                ),
                user_prompt=json.dumps(user_prompt),
                schema=indices_schema,
                model=config.get("openrouter", {}).get("model_job_links"),
                max_retries=(config.get("runtime", {}).get("retry", {}) or {}).get("max_attempts"),
            )
            selected_indices: List[int] = sel_obj.get("indices", [])

            # Debug: log anchor and selection counts
            log_event(logger, logger.level, "anchors_info", total=len(anchors), limited=len(limited_anchors), selected=len(selected_indices))

            raw_urls: List[str] = []
            for idx in selected_indices:
                if isinstance(idx, int) and 0 <= idx < len(limited_anchors):
                    raw_urls.append(limited_anchors[idx]["href"])  # type: ignore[index]

            # Heuristic fallback if LLM returns no indices: pick anchors that look like job postings
            if not raw_urls:
                heuristic_matches: List[str] = []
                keywords = [
                    "/careers/",
                    "/career/",
                    "/jobs/",
                    "/job/",
                    "/positions/",
                    "/position/",
                    "/opportunities/",
                    "/opportunity/",
                    "/opening/",
                    "/openings/",
                    "greenhouse.io",
                    "lever.co",
                    "ashbyhq.com",
                    "workable.com",
                ]
                exclude_substrings = [
                    "?",
                    "#",
                    "/teams",
                    "/departments",
                    "/locations",
                    "/search",
                    "/filters",
                    "/pages/",
                    "/page/",
                    "/category/",
                    "/categories/",
                ]
                for a in limited_anchors:
                    href = a.get("href") or ""
                    text = (a.get("text") or "").lower()
                    if not href:
                        continue
                    href_l = href.lower()
                    if any(x in href_l for x in keywords) and not any(x in href_l for x in exclude_substrings):
                        # simple signal: looks like a job posting link
                        heuristic_matches.append(href)
                    elif any(t in text for t in ["apply", "view role", "view job", "see role", "see job"]):
                        heuristic_matches.append(href)

                if heuristic_matches:
                    raw_urls = heuristic_matches
                    log_event(logger, logger.level, "heuristic_fallback_used", matches=len(heuristic_matches))
            job_urls = absolutize_and_dedupe_urls(raw_urls, base_url=careers_url)
            # If still empty, try ATS board fallback: detect external board link and re-scrape
            if not job_urls:
                board_hosts = ["greenhouse.io", "lever.co", "ashbyhq.com", "workable.com", "jobs.ashbyhq.com"]
                board_links = [a.get("href") for a in limited_anchors if isinstance(a.get("href"), str) and any(h in a.get("href", "").lower() for h in board_hosts)]
                board_links = absolutize_and_dedupe_urls([u for u in board_links if u], base_url=careers_url)
                if board_links:
                    fallback_board = board_links[0]
                    log_event(logger, logger.level, "ats_board_fallback", url=fallback_board)
                    try:
                        board_page = firecrawl.fetch_page(fallback_board)
                        board_anchors = extract_anchors_from_page_data(board_page)[:200]
                        # Heuristic on board page
                        board_raw: List[str] = []
                        for a in board_anchors:
                            href = a.get("href") or ""
                            text = (a.get("text") or "").lower()
                            if not href:
                                continue
                            href_l = href.lower()
                            if any(t in text for t in ["apply", "view job", "view role", "see job", "job details"]) and "/job" in href_l:
                                board_raw.append(href)
                            elif any(seg in href_l for seg in ["/job/", "/jobs/", "/positions/", "/careers/"]):
                                board_raw.append(href)
                        job_urls = absolutize_and_dedupe_urls(board_raw, base_url=fallback_board)
                    except Exception as e:  # noqa: BLE001
                        log_event(logger, logger.level, "ats_board_error", url=fallback_board, error=str(e))

            totals["job_urls_found"] += len(job_urls)
            if job_urls:
                # Log a small sample of selected URLs to aid debugging
                sample = job_urls[:3]
                log_event(logger, logger.level, "job_urls_selected", count=len(job_urls), sample=sample)

            if max_jobs is not None:
                job_urls = job_urls[:max_jobs]

            def process_job(job_url: str) -> Optional[List[Any]]:
                try:
                    if resume and cache.is_job_seen(job_url):
                        return None

                    canonical = job_url
                    page_html = ""
                    fields: Optional[Dict[str, Any]] = None

                    if bamboohr_parser.can_handle(job_url):
                        try:
                            parsed = bamboohr_parser.parse_job(job_url)
                            parsed_fields = parsed.get("fields")
                            if isinstance(parsed_fields, dict):
                                fields = parsed_fields
                                canonical = parsed.get("canonical") or canonical
                                page_html = parsed.get("page_html", "") or ""
                                log_event(
                                    logger,
                                    logger.level,
                                    "ats_parser_used",
                                    url=job_url,
                                    parser="bamboohr",
                                )
                        except Exception as parser_exc:  # noqa: BLE001
                            log_event(
                                logger,
                                logger.level,
                                "ats_parser_error",
                                url=job_url,
                                error=str(parser_exc),
                            )

                    if fields is None:
                        job_page = firecrawl.fetch_page(job_url)
                        html = job_page.get("html", "")
                        canonical = job_page.get("canonical") or canonical
                        page_html = html

                        fields_schema = llm.load_schema(Path("schemas/job_fields.schema.json"))
                        fields_schema_text = json.dumps(fields_schema, separators=(",", ":"))

                        user_payload = {
                            "Job URL": job_url,
                            "Canonical URL": canonical,
                            "HTML": html[:250000],  # truncate large pages
                            "Notes": (
                                "Common signals: ‘Apply’, ‘Responsibilities’, ‘Qualifications’. "
                                "Words like ‘Remote’/‘Hybrid’ may influence remote_ok."
                            ),
                        }

                        # Embed the JSON Schema text directly in the system prompt to ensure the model sees it
                        fields = llm.complete_json(
                            system_prompt=(
                                "You are an expert ATS parser. Return ONLY a JSON object that conforms to this JSON Schema (Draft 2020-12):\n"
                                f"{fields_schema_text}\n"
                                "Rules: Prefer exact strings from the page for title and location. "
                                "remote_ok must be boolean; infer only if clearly stated. "
                                "job_type must be one of: Full Time, Part Time, Internship. "
                                "description_html must be HTML of the job description (not full page). "
                                "If salary not present, set both salaries to null. "
                                "application_link should be the primary apply URL; fall back to the job page URL if none. "
                                "Do not include markdown, code fences, or explanations."
                            ),
                            user_prompt=json.dumps(user_payload),
                            schema=fields_schema,
                            model=config.get("openrouter", {}).get("model_job_fields"),
                            max_retries=(config.get("runtime", {}).get("retry", {}) or {}).get("max_attempts"),
                        )

                    if fields is None:
                        raise ValueError("Failed to extract job fields")

                    fields = postprocess_fields(
                        fields,
                        company_override=company_override,
                        page_html=page_html,
                        job_url=job_url,
                        canonical_url=canonical,
                    )

                    row = to_sheet_row(fields)
                    fp = fingerprint(canonical, fields.get("title", ""), fields.get("company_name", ""))

                    if cache.is_fingerprint_seen(fp):
                        totals["duplicates"] += 1
                        return None

                    cache.mark_job_seen(job_url, canonical, fields.get("title"), fields.get("company_name"), fp)

                    return row
                except Exception as e:  # noqa: BLE001
                    log_event(logger, logger.level, "job_error", url=job_url, error=str(e))
                    totals["errors"] += 1
                    if collect_errors:
                        errors.append({
                            "scope": "job",
                            "url": job_url,
                            "message": str(e),
                        })
                    return None

            results: List[Optional[List[Any]]] = []
            with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
                for res in executor.map(process_job, job_urls):
                    results.append(res)

            rows: List[List[Any]] = [r for r in results if r]
            if not dry_run and sheets and rows:
                appended = sheets.append_rows(rows)
                totals["rows_appended"] += appended
            elif dry_run and rows:
                # Write to CSV locally for inspection
                out_path = Path("data/dry_run.csv")
                out_path.parent.mkdir(parents=True, exist_ok=True)
                write_header = not out_path.exists()
                with out_path.open("a", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    if write_header:
                        writer.writerow([
                            "Title",
                            "Company Name",
                            "Location Name",
                            "Remote OK",
                            "Job Type",
                            "Description",
                            "Minimum Salary",
                            "Maximum Salary",
                            "Application Link",
                        ])
                    writer.writerows(rows)
                totals["rows_appended"] += len(rows)

            totals["careers_processed"] += 1
        except Exception as e:  # noqa: BLE001
            log_event(get_logger(), get_logger().level, "careers_error", url=careers_url, error=str(e))
            totals["errors"] += 1
            if collect_errors:
                errors.append({
                    "scope": "careers",
                    "url": careers_url,
                    "message": str(e),
                })

    result_payload: Dict[str, Any] = {"totals": totals}
    if collect_errors:
        result_payload["errors"] = errors

    if emit_stdout:
        print(json.dumps(result_payload))
    return result_payload
    return totals


def main() -> None:
    parser = argparse.ArgumentParser(description="Jobs Page → Google Sheet Pipeline")
    parser.add_argument("--sheet-id", dest="sheet_id", default=None)
    parser.add_argument("--worksheet", dest="worksheet", default=None)
    parser.add_argument("--url", dest="url", default=None)
    parser.add_argument("--input", dest="input_file", default=None)
    parser.add_argument("--company", dest="company", default=None)
    parser.add_argument("--config", dest="config", default=None)
    parser.add_argument("--dry-run", dest="dry_run", action="store_true")
    parser.add_argument("--resume", dest="resume", action="store_true")
    parser.add_argument("--concurrency", dest="concurrency", type=int, default=None)
    parser.add_argument("--max-jobs", dest="max_jobs", type=int, default=None)
    parser.add_argument("--env-file", dest="env_file", default=None, help="Path to .env file (default: .env if present)")

    args = parser.parse_args()

    # Load environment variables from .env (optional override path)
    if args.env_file:
        load_dotenv(args.env_file)
    else:
        load_dotenv()

    cfg = load_config(args.config)
    runtime = cfg.get("runtime", {})

    careers_urls = resolve_input(args.url or runtime.get("single_url"), args.input_file or runtime.get("input_file"))
    if not careers_urls:
        print("Provide --url or --input file with at least one careers URL", file=sys.stderr)
        sys.exit(2)

    run_pipeline(
        config=cfg,
        careers_urls=careers_urls,
        sheet_id=args.sheet_id or cfg.get("google_sheets", {}).get("spreadsheet_id"),
        worksheet=args.worksheet or cfg.get("google_sheets", {}).get("worksheet_name"),
        company_override=args.company or runtime.get("company_override"),
        dry_run=bool(args.dry_run),
        resume=bool(args.resume),
        concurrency=args.concurrency or runtime.get("concurrency", 8),
        max_jobs=args.max_jobs,
    )


if __name__ == "__main__":
    main()


