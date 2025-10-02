from __future__ import annotations

import os
import time
from typing import Any, Dict, Optional, List

import httpx


class FirecrawlClient:
    def __init__(self, cfg: Dict[str, Any]):
        self.api_key_env: str = cfg.get("api_key_env", "FIRECRAWL_API_KEY")
        self.api_key: Optional[str] = os.getenv(self.api_key_env)
        self.render_js: bool = bool(cfg.get("render_js", True))
        self.extract_links: bool = bool(cfg.get("extract_links", True))
        self.request_timeout: int = int(cfg.get("request_timeout", 30))
        # Optional scraping options
        self.max_age_ms: Optional[int] = cfg.get("max_age_ms")  # 0 forces fresh
        self.only_main_content: Optional[bool] = cfg.get("only_main_content")
        self.wait_ms: Optional[int] = cfg.get("wait_ms")  # e.g., 1500 to allow JS to render
        # Base URL inferred from Firecrawl docs; adjust if different
        self.base_url: str = cfg.get("base_url", "https://api.firecrawl.dev")
        # Rate limiting
        self.rate_limit_delay: float = float(cfg.get("rate_limit_delay", 1.0))  # seconds between requests
        self._last_request_time: float = 0.0

        if not self.api_key:
            raise ValueError(
                f"Missing Firecrawl API key. Set environment variable '{self.api_key_env}' before running the pipeline."
            )

    def _headers(self) -> Dict[str, str]:
        headers = {"User-Agent": "industrion-scraping/1.0 (+contact@yourdomain.com)"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        headers["Content-Type"] = "application/json"
        return headers

    def _normalize_links(self, raw_links: Any) -> List[Dict[str, str]]:
        if not raw_links:
            return []
        normalized: List[Dict[str, str]] = []
        if isinstance(raw_links, list):
            for item in raw_links:
                if isinstance(item, str):
                    normalized.append({"href": item, "text": ""})
                elif isinstance(item, dict):
                    href = item.get("href") or item.get("url") or item.get("link") or ""
                    text = item.get("text") or item.get("label") or item.get("title") or ""
                    if href:
                        normalized.append({"href": href, "text": text})
        elif isinstance(raw_links, str):
            # Treat a single string as one link
            normalized.append({"href": raw_links, "text": ""})
        return normalized

    def _enforce_rate_limit(self) -> None:
        """Enforce rate limiting between requests."""
        current_time = time.time()
        time_since_last = current_time - self._last_request_time
        if time_since_last < self.rate_limit_delay:
            sleep_time = self.rate_limit_delay - time_since_last
            time.sleep(sleep_time)
        self._last_request_time = time.time()

    def fetch_page(self, url: str) -> Dict[str, Any]:
        # Enforce rate limiting
        self._enforce_rate_limit()

        # Firecrawl v2 expects POST /v2/scrape with JSON body
        # Request both html and links to satisfy downstream parsing
        payload: Dict[str, Any] = {
            "url": url,
            "formats": ["html", "links"],
        }
        # Apply optional options when provided
        if self.max_age_ms is not None:
            payload["maxAge"] = int(self.max_age_ms)
        if isinstance(self.only_main_content, bool):
            payload["onlyMainContent"] = self.only_main_content
        # Use actions.wait to give JS time to render when desired
        if isinstance(self.wait_ms, int) and self.wait_ms > 0:
            payload["actions"] = [{"type": "wait", "milliseconds": int(self.wait_ms)}]

        endpoint = f"{self.base_url}/v2/scrape"
        with httpx.Client(timeout=self.request_timeout) as client:
            try:
                resp = client.post(endpoint, json=payload, headers=self._headers())
                resp.raise_for_status()

                # Handle potential JSON parsing errors
                try:
                    data_json = resp.json()
                except ValueError as json_err:
                    # Log the raw response for debugging
                    raw_text = resp.text[:500] if resp.text else "No response text"
                    raise ValueError(
                        f"Failed to parse Firecrawl JSON response for {url}. "
                        f"Status: {resp.status_code}, Raw response start: {raw_text}"
                    ) from json_err

            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:
                    # If we hit rate limit, wait longer and retry once
                    time.sleep(5.0)
                    resp = client.post(endpoint, json=payload, headers=self._headers())
                    resp.raise_for_status()
                    try:
                        data_json = resp.json()
                    except ValueError as json_err:
                        raw_text = resp.text[:500] if resp.text else "No response text"
                        raise ValueError(
                            f"Failed to parse Firecrawl JSON response for {url} after retry. "
                            f"Status: {resp.status_code}, Raw response start: {raw_text}"
                        ) from json_err
                elif e.response.status_code >= 500:
                    # Server error, wait and retry once
                    time.sleep(10.0)
                    resp = client.post(endpoint, json=payload, headers=self._headers())
                    resp.raise_for_status()
                    try:
                        data_json = resp.json()
                    except ValueError as json_err:
                        raw_text = resp.text[:500] if resp.text else "No response text"
                        raise ValueError(
                            f"Failed to parse Firecrawl JSON response for {url} after server error retry. "
                            f"Status: {resp.status_code}, Raw response start: {raw_text}"
                        ) from json_err
                else:
                    raise

        if not isinstance(data_json, dict):
            raise ValueError(f"Unexpected response type from Firecrawl: {type(data_json).__name__}")

        # Error handling per v2 shape: { success: bool, error?: string }
        success = data_json.get("success", True)
        if not success:
            error_msg = data_json.get("error") or data_json.get("message") or "Firecrawl returned success=false"
            raise ValueError(f"Firecrawl error: {error_msg}")

        data_obj = data_json.get("data")
        if not isinstance(data_obj, dict):
            # Some responses might return the content at the root
            data_obj = data_json if isinstance(data_json, dict) else {}

        html = data_obj.get("html") or ""
        links_raw = data_obj.get("links") or []
        links = self._normalize_links(links_raw)
        metadata = data_obj.get("metadata") or {}
        canonical = metadata.get("sourceURL") or metadata.get("canonical") or None

        # For backward compatibility with any callers expecting text
        text = ""

        return {"html": html, "text": text, "links": links, "canonical": canonical}


