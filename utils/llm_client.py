from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
import jsonschema


class OpenRouterClient:
    def __init__(self, cfg: Dict[str, Any]):
        self.api_key_env: str = cfg.get("api_key_env", "OPENROUTER_API_KEY")
        self.api_key: Optional[str] = os.getenv(self.api_key_env)
        self.max_tokens: int = int(cfg.get("max_tokens", 2000))
        self.temperature: float = float(cfg.get("temperature", 0.2))
        self.timeout_seconds: int = int(cfg.get("timeout_seconds", 60))
        # Default retry budget for JSON completions; can be overridden per-call
        self.max_retries: int = int(cfg.get("max_retries", 4))
        self.base_url: str = cfg.get("base_url", "https://openrouter.ai/api/v1")
        self.site_url: Optional[str] = cfg.get("site_url")  # Optional: improves routing/limits on OpenRouter
        self.site_title: Optional[str] = cfg.get("site_title")  # Optional: improves routing/limits on OpenRouter
        # Rate limiting
        self.rate_limit_delay: float = float(cfg.get("rate_limit_delay", 0.5))  # seconds between requests
        self._last_request_time: float = 0.0

        if not self.api_key:
            raise ValueError(
                f"Missing API key for OpenRouter. Set environment variable '{self.api_key_env}'."
            )

    def _headers(self) -> Dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        # Recommended but optional headers for OpenRouter
        if self.site_url:
            headers["HTTP-Referer"] = self.site_url
        if self.site_title:
            headers["X-Title"] = self.site_title
        return headers

    def load_schema(self, path: Path) -> Dict[str, Any]:
        import json
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def _enforce_rate_limit(self) -> None:
        """Enforce rate limiting between requests."""
        current_time = time.time()
        time_since_last = current_time - self._last_request_time
        if time_since_last < self.rate_limit_delay:
            sleep_time = self.rate_limit_delay - time_since_last
            time.sleep(sleep_time)
        self._last_request_time = time.time()

    def _post_chat(self, model: Optional[str], system_prompt: str, user_prompt: str, expect_json: bool = False) -> str:
        # Enforce rate limiting
        self._enforce_rate_limit()

        payload = {
            # Use a widely-available default model slug on OpenRouter
            "model": model or "google/gemini-2.5-pro",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if expect_json:
            # Prefer structured JSON responses when supported
            payload["response_format"] = {"type": "json_object"}
        endpoint = f"{self.base_url}/chat/completions"
        with httpx.Client(timeout=self.timeout_seconds) as client:
            try:
                resp = client.post(endpoint, json=payload, headers=self._headers())
                resp.raise_for_status()
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:
                    # If we hit rate limit, wait longer and retry once
                    time.sleep(10.0)
                    resp = client.post(endpoint, json=payload, headers=self._headers())
                    resp.raise_for_status()
                else:
                    # Surface response details for easier debugging (e.g., bad model slug)
                    detail = None
                    try:
                        detail = e.response.json()
                    except Exception:  # noqa: BLE001
                        detail = e.response.text if e.response is not None else str(e)
                    raise ValueError(
                        f"OpenRouter chat completion failed: {e}. Details: {detail}"
                    ) from e
            data = resp.json()
        content = data["choices"][0]["message"]["content"]
        return content

    def complete_json(self, system_prompt: str, user_prompt: str, schema: Dict[str, Any], model: Optional[str] = None, max_retries: Optional[int] = None) -> Dict[str, Any]:
        import json

        attempts = 0
        last_error: Optional[str] = None
        # Use per-call override if provided; otherwise fall back to client default
        retries_budget = self.max_retries if max_retries is None else max_retries
        while attempts <= retries_budget:
            attempts += 1
            content = self._post_chat(
                model=model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                expect_json=True,
            )
            # Try to parse JSON from content
            cleaned = self._extract_json_text(content)
            try:
                obj = json.loads(cleaned)
                jsonschema.validate(instance=obj, schema=schema)
                return obj
            except json.JSONDecodeError as e:  # noqa: BLE001
                # Capture a detailed snippet of the model output to aid debugging
                snippet = cleaned[:500].replace("\n", "\\n")
                last_error = f"JSON parsing failed: {e} | Raw content snippet (500 chars): \"{snippet}\""
            except Exception as e:  # noqa: BLE001
                # Capture a short snippet of the model output to aid debugging
                snippet = cleaned[:280].replace("\n", "\\n")
                last_error = f"{e} | snippet=\"{snippet}\""
                # tighten instruction and retry
                user_prompt = (
                    user_prompt
                    + "\n\nReturn ONLY a valid JSON object that conforms to the schema. "
                    + "Do not include markdown, code fences, or any explanation."
                )
                time.sleep(0.75 * attempts)
                continue
        raise ValueError(f"LLM failed to produce valid JSON after retries: {last_error}")


    def _extract_json_text(self, text: str) -> str:
        """
        Extract the first valid JSON object or array substring from an arbitrary LLM response.
        - Strips surrounding code fences if present
        - Scans for balanced {...} or [...] while respecting string escapes
        Returns the raw JSON substring if found; otherwise returns the original trimmed text.
        """
        s = text.strip()

        # Strip code fences if the whole response is fenced
        if s.startswith("```") and s.endswith("```"):
            inner = s[3:-3].strip()
            # Remove language hint like 'json' on the first line
            first_newline = inner.find("\n")
            if first_newline != -1:
                first_line = inner[:first_newline].strip().lower()
                if first_line in {"json", "javascript", "ts", "text"}:
                    inner = inner[first_newline + 1 :]
            s = inner.strip()

        # Brace-aware scan
        in_string = False
        escape = False
        depth = 0
        start_idx: Optional[int] = None
        mode_closer: Optional[str] = None  # '}' or ']'

        for i, ch in enumerate(s):
            if start_idx is None:
                if ch == '{':
                    start_idx = i
                    depth = 1
                    mode_closer = '}'
                    continue
                if ch == '[':
                    start_idx = i
                    depth = 1
                    mode_closer = ']'
                    continue

            # Once started, we need to track strings and escapes
            if start_idx is not None:
                if in_string:
                    if escape:
                        escape = False
                    elif ch == '\\':
                        escape = True
                    elif ch == '"':
                        in_string = False
                else:
                    if ch == '"':
                        in_string = True
                    elif ch == '{' and mode_closer == '}':
                        depth += 1
                    elif ch == '}' and mode_closer == '}':
                        depth -= 1
                        if depth == 0:
                            return s[start_idx : i + 1]
                    elif ch == '[' and mode_closer == ']':
                        depth += 1
                    elif ch == ']' and mode_closer == ']':
                        depth -= 1
                        if depth == 0:
                            return s[start_idx : i + 1]

        # Fallback: try to find fenced json inside text
        fence_start = s.find("```json")
        if fence_start != -1:
            fence_end = s.find("```", fence_start + 7)
            if fence_end != -1:
                candidate = s[fence_start + 7 : fence_end].strip()
                return candidate

        return s

