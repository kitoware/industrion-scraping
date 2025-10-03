from __future__ import annotations

import json
from http import HTTPStatus
from typing import Any, Dict, Optional

from dotenv import load_dotenv

from jobs_pipeline import load_config, resolve_input, run_pipeline


DEFAULT_HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
}


def _json_response(
    status: HTTPStatus,
    payload: Dict[str, Any],
    headers: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    response_headers: Dict[str, Any] = {**DEFAULT_HEADERS}
    if headers:
        response_headers.update(headers)
    return {
        "statusCode": int(status.value),
        "headers": response_headers,
        "body": json.dumps(payload),
    }


def handler(request: Any) -> Dict[str, Any]:
    raw_method = getattr(request, "method", "")
    if callable(raw_method):
        try:
            raw_method = raw_method()
        except TypeError:
            raw_method = raw_method(request)
    if isinstance(raw_method, bytes):
        raw_method = raw_method.decode("utf-8", "ignore")
    method = str(raw_method).strip().upper() if raw_method else ""

    if method == "OPTIONS":
        return {
            "statusCode": int(HTTPStatus.NO_CONTENT.value),
            "headers": {
                **DEFAULT_HEADERS,
                "Allow": "POST, OPTIONS",
            },
            "body": "",
        }
    if method == "HEAD":
        return {
            "statusCode": int(HTTPStatus.NO_CONTENT.value),
            "headers": {
                **DEFAULT_HEADERS,
                "Allow": "POST, OPTIONS",
            },
            "body": "",
        }
    if method != "POST":
        return _json_response(
            HTTPStatus.METHOD_NOT_ALLOWED,
            {"error": "Method Not Allowed"},
            {"Allow": "POST, OPTIONS"},
        )

    try:
        payload = request.json()  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        return _json_response(HTTPStatus.BAD_REQUEST, {"error": "Invalid JSON body"})

    if not isinstance(payload, dict):
        return _json_response(HTTPStatus.BAD_REQUEST, {"error": "Body must be a JSON object"})

    url = str(payload.get("url", "")).strip()
    if not url:
        return _json_response(HTTPStatus.BAD_REQUEST, {"error": "Field 'url' is required"})

    try:
        from urllib.parse import urlparse

        parsed_url = urlparse(url)
        if not (parsed_url.scheme in {"http", "https"} and parsed_url.netloc):
            return _json_response(HTTPStatus.BAD_REQUEST, {"error": "Invalid URL supplied"})
        load_dotenv(payload.get("envFile"))
        config_path = payload.get("configPath")
        cfg = load_config(config_path if isinstance(config_path, str) and config_path else None)
        careers_urls = resolve_input(url, None)

        dry_run = bool(payload.get("dryRun", not payload.get("sheetId")))
        sheet_id: Optional[str] = payload.get("sheetId")
        worksheet: Optional[str] = payload.get("worksheet")
        company_override: Optional[str] = payload.get("company")

        max_jobs: Optional[int] = None
        if (payload_max := payload.get("maxJobs")) is not None:
            try:
                parsed = int(payload_max)
                if parsed > 0:
                    max_jobs = min(parsed, 50)
            except (TypeError, ValueError):
                return _json_response(HTTPStatus.BAD_REQUEST, {"error": "maxJobs must be a positive integer"})

        concurrency = payload.get("concurrency")
        try:
            parsed_concurrency = int(concurrency) if concurrency is not None else None
        except (TypeError, ValueError):
            return _json_response(HTTPStatus.BAD_REQUEST, {"error": "concurrency must be an integer"})

        if parsed_concurrency is not None and parsed_concurrency <= 0:
            return _json_response(HTTPStatus.BAD_REQUEST, {"error": "concurrency must be greater than zero"})

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
        )
        totals = result.get("totals") if isinstance(result, dict) else None
        if not isinstance(totals, dict):
            return _json_response(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": "Pipeline returned an unexpected totals payload"},
            )

        errors = result.get("errors") if isinstance(result, dict) else None
        normalized_errors = errors if isinstance(errors, list) else []

        if totals.get("careers_processed", 0) == 0 and normalized_errors:
            first_error = normalized_errors[0]
            message = first_error.get("message") if isinstance(first_error, dict) else str(first_error)
            return _json_response(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {
                    "error": message or "Pipeline reported an error",
                    "totals": totals,
                    "errors": normalized_errors,
                    "dryRun": dry_run,
                },
            )

        payload = {"totals": totals, "dryRun": dry_run}
        if normalized_errors:
            payload["errors"] = normalized_errors

        return _json_response(HTTPStatus.OK, payload)
    except ValueError as exc:
        return _json_response(
            HTTPStatus.BAD_REQUEST,
            {"error": "Pipeline validation failed", "details": str(exc)},
        )
    except SystemExit as exc:
        return _json_response(HTTPStatus.BAD_REQUEST, {"error": f"Pipeline exit: {exc}"})
    except Exception as exc:  # noqa: BLE001
        return _json_response(
            HTTPStatus.INTERNAL_SERVER_ERROR,
            {"error": "Pipeline execution failed", "details": str(exc)},
        )
