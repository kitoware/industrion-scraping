from __future__ import annotations

import os
from typing import Any, List, Optional
import json
import base64

import gspread
from google.oauth2.service_account import Credentials


HEADER = [
    "Title",
    "Company Name",
    "Location Name",
    "Remote OK",
    "Job Type",
    "Description",
    "Minimum Salary",
    "Maximum Salary",
    "Application Link",
]


class SheetsClient:
    def __init__(self, spreadsheet_id: str, worksheet_name: str, cfg: dict):
        # Normalize spreadsheet_id to bare key (supports full URLs and trailing slashes)
        normalized_id = (spreadsheet_id or "").strip()
        if normalized_id.endswith("/"):
            normalized_id = normalized_id[:-1]
        if "docs.google.com" in normalized_id and "/d/" in normalized_id:
            try:
                normalized_id = normalized_id.split("/d/")[1].split("/")[0]
            except Exception:
                # Fall back to provided value if parsing fails
                pass
        self.spreadsheet_id = normalized_id
        self.worksheet_name = worksheet_name
        self.service_account_json_env = cfg.get("service_account_json_env", "GOOGLE_APPLICATION_CREDENTIALS")
        env_name = self.service_account_json_env
        raw_value = os.getenv(env_name)
        # Fallbacks: allow <NAME>_JSON or <NAME>_B64 if main var is unset
        if raw_value is None:
            raw_value = os.getenv(f"{env_name}_JSON") or os.getenv(f"{env_name}_B64")

        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]

        creds: Optional[Credentials]
        creds = None

        # Case 1: value is a filesystem path
        if raw_value and os.path.exists(raw_value):
            creds = Credentials.from_service_account_file(raw_value, scopes=scopes)
        else:
            # Case 2: value contains raw JSON
            if raw_value:
                parsed: Optional[dict] = None
                # Try direct JSON
                try:
                    parsed = json.loads(raw_value)
                except Exception:
                    parsed = None
                # Try base64 → JSON if direct JSON failed
                if parsed is None:
                    try:
                        decoded = base64.b64decode(raw_value).decode("utf-8")
                        parsed = json.loads(decoded)
                    except Exception:
                        parsed = None
                if parsed:
                    creds = Credentials.from_service_account_info(parsed, scopes=scopes)

        if creds is None:
            raise RuntimeError(
                "Service account credentials not found or invalid. "
                f"Set '{env_name}' to a file path, raw JSON, or base64-encoded JSON, "
                f"or use '{env_name}_JSON' / '{env_name}_B64'."
            )

        self.gc = gspread.authorize(creds)
        self.sh = self.gc.open_by_key(self.spreadsheet_id)
        try:
            self.ws = self.sh.worksheet(self.worksheet_name)
        except gspread.WorksheetNotFound:
            self.ws = self.sh.add_worksheet(title=self.worksheet_name, rows=1000, cols=9)

    def ensure_header(self) -> None:
        values = self.ws.get_all_values()
        if not values:
            self.ws.append_row(HEADER)
            self.ws.freeze(rows=1)

    def append_rows(self, rows: List[List[Any]]) -> int:
        # Batch append via insert_rows to reduce API calls
        start_row_index = len(self.ws.get_all_values()) + 1
        self.ws.insert_rows(rows, row=start_row_index)
        return len(rows)


