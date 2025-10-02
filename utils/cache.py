from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional


class Cache:
    def __init__(self, path: str):
        self.path = path
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.path) as conn:
            c = conn.cursor()
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS careers_pages (
                    url TEXT PRIMARY KEY,
                    last_fetched_at TIMESTAMP,
                    status TEXT
                )
                """
            )
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    url TEXT PRIMARY KEY,
                    canonical_url TEXT,
                    title TEXT,
                    company TEXT,
                    fingerprint TEXT,
                    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            c.execute("CREATE INDEX IF NOT EXISTS idx_jobs_fp ON jobs(fingerprint)")
            conn.commit()

    def is_job_seen(self, url: str) -> bool:
        with sqlite3.connect(self.path) as conn:
            c = conn.cursor()
            c.execute("SELECT 1 FROM jobs WHERE url = ? LIMIT 1", (url,))
            return c.fetchone() is not None

    def is_fingerprint_seen(self, fp: str) -> bool:
        with sqlite3.connect(self.path) as conn:
            c = conn.cursor()
            c.execute("SELECT 1 FROM jobs WHERE fingerprint = ? LIMIT 1", (fp,))
            return c.fetchone() is not None

    def mark_job_seen(self, url: str, canonical_url: Optional[str], title: Optional[str], company: Optional[str], fingerprint: Optional[str]) -> None:
        with sqlite3.connect(self.path) as conn:
            c = conn.cursor()
            c.execute(
                "INSERT OR REPLACE INTO jobs(url, canonical_url, title, company, fingerprint) VALUES(?,?,?,?,?)",
                (url, canonical_url, title, company, fingerprint),
            )
            conn.commit()


