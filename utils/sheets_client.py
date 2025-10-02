from __future__ import annotations

import os
from typing import Any, List, Optional

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
        sa_path = os.getenv(self.service_account_json_env)
        if not sa_path or not os.path.exists(sa_path):
            raise RuntimeError(
                f"Service account json not found at env {self.service_account_json_env}. Set path to JSON file."
            )

        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = Credentials.from_service_account_file(sa_path, scopes=scopes)
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


