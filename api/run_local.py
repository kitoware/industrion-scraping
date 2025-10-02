from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from jobs_pipeline import load_config, resolve_input, run_pipeline


def execute(payload: Dict[str, Any]) -> Dict[str, Any]:
    url = str(payload.get("url", "")).strip()
    if not url:
        raise ValueError("Field 'url' is required")

    load_dotenv(payload.get("envFile") or None)
    config_path = payload.get("configPath")
    cfg = load_config(config_path if isinstance(config_path, str) and config_path else None)
    careers_urls = resolve_input(url, None)

    dry_run = bool(payload.get("dryRun", not payload.get("sheetId")))
    sheet_id: Optional[str] = payload.get("sheetId")
    worksheet: Optional[str] = payload.get("worksheet")
    company_override: Optional[str] = payload.get("company")

    max_jobs: Optional[int] = None
    if (payload_max := payload.get("maxJobs")) is not None:
        parsed = int(payload_max)
        if parsed > 0:
            max_jobs = parsed

    concurrency = payload.get("concurrency")
    parsed_concurrency = int(concurrency) if concurrency else None

    runtime_cfg = cfg.get("runtime", {}) if isinstance(cfg, dict) else {}
    default_concurrency = runtime_cfg.get("concurrency", 4)
    safe_concurrency = min(parsed_concurrency or default_concurrency, 4)

    result = run_pipeline(
        config=cfg,
        careers_urls=careers_urls,
        sheet_id=sheet_id or cfg.get("google_sheets", {}).get("spreadsheet_id") if isinstance(cfg, dict) else None,
        worksheet=worksheet or cfg.get("google_sheets", {}).get("worksheet_name") if isinstance(cfg, dict) else None,
        company_override=company_override or runtime_cfg.get("company_override"),
        dry_run=dry_run,
        resume=False,
        concurrency=safe_concurrency,
        max_jobs=max_jobs,
        emit_stdout=False,
    )
    totals = result.get("totals") if isinstance(result, dict) else None
    if not isinstance(totals, dict):
        raise ValueError("Pipeline returned an unexpected totals payload")

    errors = result.get("errors") if isinstance(result, dict) else None
    normalized_errors = errors if isinstance(errors, list) else []

    response_payload: Dict[str, Any] = {"totals": totals, "dryRun": dry_run}
    if normalized_errors:
        response_payload["errors"] = []
        for entry in normalized_errors:
            if isinstance(entry, dict):
                response_payload["errors"].append({
                    "scope": entry.get("scope"),
                    "url": entry.get("url"),
                    "message": entry.get("message", ""),
                })
            else:
                response_payload["errors"].append({"scope": None, "url": None, "message": str(entry)})

        if totals.get("careers_processed", 0) == 0:
            primary = response_payload["errors"][0]
            response_payload["error"] = primary.get("message") or "Pipeline reported an error"

    return response_payload


def main() -> None:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        result = execute(payload)
        sys.stdout.write(json.dumps(result))
    except SystemExit as exc:
        error = {"error": f"Pipeline exit: {exc}"}
        sys.stdout.write(json.dumps(error))
        sys.exit(1)
    except Exception as exc:  # noqa: BLE001
        error = {"error": str(exc)}
        sys.stdout.write(json.dumps(error))
        sys.exit(1)


if __name__ == "__main__":
    main()
