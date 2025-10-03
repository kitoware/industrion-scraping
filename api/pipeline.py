from __future__ import annotations

import json
import sys
from pathlib import Path
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from typing import Any, Dict, Optional, Tuple

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




def _run_pipeline(payload: Dict[str, Any]) -> Tuple[HTTPStatus, Dict[str, Any]]:
    try:
        result = run_local_execute(payload)
    except ValueError as exc:
        return HTTPStatus.BAD_REQUEST, {"error": "Pipeline validation failed", "details": str(exc)}
    except SystemExit as exc:
        return HTTPStatus.BAD_REQUEST, {"error": f"Pipeline exit: {exc}"}
    except Exception as exc:  # noqa: BLE001
        return HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Pipeline execution failed", "details": str(exc)}

    if not isinstance(result, dict):
        return HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Pipeline returned invalid payload"}

    totals = result.get("totals")
    if not isinstance(totals, dict):
        return HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Pipeline returned invalid totals"}

    errors = result.get("errors") if isinstance(result.get("errors"), list) else []
    normalized = _normalize_payload(result)

    if totals.get("careers_processed", 0) == 0 and errors:
        first_error = errors[0]
        message = first_error.get("message") if isinstance(first_error, dict) else str(first_error)
        normalized["error"] = message or normalized.get("error") or "Pipeline reported an error"
        return HTTPStatus.INTERNAL_SERVER_ERROR, normalized

    return HTTPStatus.OK, normalized


class Handler(BaseHTTPRequestHandler):
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

        status, body = _run_pipeline(payload)
        self._set_headers(status)
        self._write_body(body)


PipelineHandler = Handler


__all__ = ["Handler", "PipelineHandler"]
