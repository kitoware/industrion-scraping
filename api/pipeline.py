from __future__ import annotations

import json
import sys
from pathlib import Path
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.run_local import execute as run_local_execute


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


def _normalize_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    response: Dict[str, Any] = {
        "totals": payload.get("totals"),
        "dryRun": payload.get("dryRun"),
    }

    errors = payload.get("errors")
    if isinstance(errors, list):
        response["errors"] = errors

    if payload.get("error"):
        response["error"] = payload["error"]

    return response


def handler(request: Any) -> Dict[str, Any]:
    raw_method = getattr(request, "method", "")
    original_method = raw_method
    if callable(raw_method):
        try:
            raw_method = raw_method()
        except TypeError:
            raw_method = raw_method(request)

    if isinstance(raw_method, dict):
        raw_method = raw_method.get("httpMethod") or raw_method.get("method")

    if not raw_method:
        fallback_method = None
        if isinstance(request, dict):
            fallback_method = request.get("httpMethod") or request.get("method")
        elif hasattr(request, "get"):
            try:
                fallback_method = request.get("httpMethod") or request.get("method")
            except Exception:  # noqa: BLE001
                fallback_method = None
        raw_method = fallback_method or raw_method

    if not raw_method:
        attr_method = getattr(request, "httpMethod", None) or getattr(request, "http_method", None)
        if callable(attr_method):
            try:
                raw_method = attr_method()
            except TypeError:
                raw_method = attr_method(request)
        elif attr_method:
            raw_method = attr_method

    if isinstance(raw_method, dict):
        raw_method = raw_method.get("httpMethod") or raw_method.get("method")

    if isinstance(raw_method, bytes):
        raw_method = raw_method.decode("utf-8", "ignore")

    method_details = str(raw_method if raw_method is not None else original_method)

    allowed_methods = {"POST", "OPTIONS", "HEAD"}
    method = str(raw_method).strip().upper() if raw_method else ""

    if not method and isinstance(original_method, str):
        upper_original = original_method.upper()
        for candidate in allowed_methods:
            if candidate in upper_original:
                method = candidate
                break

    if method not in allowed_methods and isinstance(raw_method, str):
        upper_current = raw_method.upper()
        for candidate in allowed_methods:
            if candidate in upper_current:
                method = candidate
                break

    if method not in allowed_methods and method_details:
        for candidate in allowed_methods:
            if candidate in method_details.upper():
                method = candidate
                break

    if not method:
        method = "POST"

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
            {"error": "Method Not Allowed", "details": {"method": method_details}},
            {"Allow": "POST, OPTIONS"},
        )

    try:
        payload = request.json()  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        return _json_response(HTTPStatus.BAD_REQUEST, {"error": "Invalid JSON body"})

    if not isinstance(payload, dict):
        return _json_response(HTTPStatus.BAD_REQUEST, {"error": "Body must be a JSON object"})

    try:
        result = run_local_execute(payload)
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

    if not isinstance(result, dict):
        return _json_response(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Pipeline returned invalid payload"})

    totals = result.get("totals")
    if not isinstance(totals, dict):
        return _json_response(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Pipeline returned invalid totals"})

    errors = result.get("errors") if isinstance(result.get("errors"), list) else []
    if totals.get("careers_processed", 0) == 0 and errors:
        first_error = errors[0]
        message = first_error.get("message") if isinstance(first_error, dict) else str(first_error)
        return _json_response(
            HTTPStatus.INTERNAL_SERVER_ERROR,
            {
                "error": message or "Pipeline reported an error",
                **_normalize_payload(result),
            },
        )

    return _json_response(HTTPStatus.OK, _normalize_payload(result))


class PipelineHandler(BaseHTTPRequestHandler):
    def _set_headers(self, status: HTTPStatus, extra_headers: Optional[Dict[str, str]] = None) -> None:
        headers = {**DEFAULT_HEADERS}
        if extra_headers:
            headers.update(extra_headers)
        self.send_response(status.value)
        for key, value in headers.items():
            self.send_header(key, str(value))
        self.end_headers()

    def _write_body(self, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.wfile.write(body)

    def _read_json_body(self) -> Optional[Dict[str, Any]]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return None
        raw_body = self.rfile.read(length).decode("utf-8")
        try:
            parsed = json.loads(raw_body)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003 - signature required by BaseHTTPRequestHandler
        return

    def do_OPTIONS(self) -> None:  # noqa: D401 - HTTP handler
        self._set_headers(HTTPStatus.NO_CONTENT, {"Allow": "POST, OPTIONS"})

    def do_HEAD(self) -> None:  # noqa: D401 - HTTP handler
        self._set_headers(HTTPStatus.NO_CONTENT, {"Allow": "POST, OPTIONS"})

    def do_POST(self) -> None:  # noqa: D401 - HTTP handler
        payload = self._read_json_body()
        if payload is None:
            self._set_headers(HTTPStatus.BAD_REQUEST)
            self._write_body({"error": "Invalid JSON body"})
            return

        try:
            result = run_local_execute(payload)
        except ValueError as exc:
            self._set_headers(HTTPStatus.BAD_REQUEST)
            self._write_body({"error": "Pipeline validation failed", "details": str(exc)})
            return
        except SystemExit as exc:
            self._set_headers(HTTPStatus.BAD_REQUEST)
            self._write_body({"error": f"Pipeline exit: {exc}"})
            return
        except Exception as exc:  # noqa: BLE001
            self._set_headers(HTTPStatus.INTERNAL_SERVER_ERROR)
            self._write_body({"error": "Pipeline execution failed", "details": str(exc)})
            return

        if not isinstance(result, dict):
            self._set_headers(HTTPStatus.INTERNAL_SERVER_ERROR)
            self._write_body({"error": "Pipeline returned invalid payload"})
            return

        totals = result.get("totals")
        if not isinstance(totals, dict):
            self._set_headers(HTTPStatus.INTERNAL_SERVER_ERROR)
            self._write_body({"error": "Pipeline returned invalid totals"})
            return

        errors = result.get("errors") if isinstance(result.get("errors"), list) else []
        status = HTTPStatus.OK
        if totals.get("careers_processed", 0) == 0 and errors:
            status = HTTPStatus.INTERNAL_SERVER_ERROR

        self._set_headers(status)
        self._write_body(_normalize_payload(result))


__all__ = ["handler", "PipelineHandler"]
