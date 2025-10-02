from __future__ import annotations

import re
from functools import lru_cache
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

import httpx


_BAMBOOHR_JOB_PATH = re.compile(r"^/careers/(?P<id>\d+)(?:/.*)?$")


def is_bamboohr_job_url(url: str) -> Optional[Tuple[str, str]]:
    """Return (base_url, job_id) if the URL points to a BambooHR job posting."""
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return None
    if not parsed.netloc.lower().endswith(".bamboohr.com"):
        return None
    match = _BAMBOOHR_JOB_PATH.match(parsed.path or "")
    if not match:
        return None
    job_id = match.group("id")
    base_url = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
    return base_url, job_id


class BambooHRParser:
    """Deterministic parser for BambooHR-hosted job postings."""

    def __init__(self, timeout: float = 20.0):
        self.timeout = timeout

    def can_handle(self, url: str) -> bool:
        return is_bamboohr_job_url(url) is not None

    def parse_job(self, url: str) -> Dict[str, Any]:
        info = is_bamboohr_job_url(url)
        if info is None:
            raise ValueError("Not a BambooHR job URL")
        base_url, job_id = info
        detail = _fetch_job_detail(base_url, job_id, self.timeout)
        job_opening = detail.get("jobOpening")
        if not isinstance(job_opening, dict):
            raise ValueError("BambooHR detail payload missing 'jobOpening'")
        company_info = _fetch_company_info(base_url, self.timeout)
        fields = _map_fields(job_opening, company_info)
        canonical = job_opening.get("jobOpeningShareUrl") or url
        return {
            "fields": fields,
            "canonical": canonical,
            "page_html": "",
        }


def _fetch_json(url: str, timeout: float) -> Any:
    try:
        response = httpx.get(url, timeout=timeout)
        response.raise_for_status()
    except httpx.HTTPError as exc:  # noqa: BLE001
        raise ValueError(f"Failed to fetch {url}: {exc}") from exc
    try:
        return response.json()
    except ValueError as exc:  # noqa: BLE001
        snippet = response.text[:200] if response.text else ""
        raise ValueError(f"Invalid JSON from {url}: {exc}; snippet={snippet!r}") from exc


@lru_cache(maxsize=64)
def _fetch_company_info(base_url: str, timeout: float) -> Dict[str, Any]:
    payload = _fetch_json(f"{base_url}/careers/company-info", timeout)
    result = payload.get("result") if isinstance(payload, dict) else None
    if not isinstance(result, dict):
        raise ValueError("BambooHR company info missing 'result'")
    return result


@lru_cache(maxsize=256)
def _fetch_job_detail(base_url: str, job_id: str, timeout: float) -> Dict[str, Any]:
    payload = _fetch_json(f"{base_url}/careers/{job_id}/detail", timeout)
    result = payload.get("result") if isinstance(payload, dict) else None
    if not isinstance(result, dict):
        raise ValueError("BambooHR job detail missing 'result'")
    return result


def _map_fields(job_opening: Dict[str, Any], company_info: Dict[str, Any]) -> Dict[str, Any]:
    title = _clean_text(job_opening.get("jobOpeningName"))
    company_name = _clean_text(company_info.get("name"))
    location = _compose_location(job_opening)
    remote_ok = _map_remote(job_opening.get("locationType"))
    job_type = _clean_text(job_opening.get("employmentStatusLabel"))
    description_html = job_opening.get("description") or ""
    min_salary, max_salary = _extract_compensation(job_opening.get("compensation"))
    application_link = job_opening.get("jobOpeningShareUrl") or ""

    return {
        "title": title,
        "company_name": company_name,
        "location": location,
        "remote_ok": remote_ok,
        "job_type": job_type,
        "description_html": description_html,
        "min_salary": min_salary,
        "max_salary": max_salary,
        "application_link": application_link,
    }


def _clean_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


def _compose_location(job_opening: Dict[str, Any]) -> str:
    location = job_opening.get("location") or {}
    ats_location = job_opening.get("atsLocation") or {}

    city = _first_non_empty(location.get("city"), ats_location.get("city"))
    state = _first_non_empty(location.get("state"), ats_location.get("state"), ats_location.get("province"))
    country = _first_non_empty(
        location.get("addressCountry"),
        ats_location.get("country"),
        ats_location.get("countryId"),
    )

    parts = [part for part in (city, state) if part]
    if not parts and country:
        parts.append(country)
    elif country and country not in parts:
        parts.append(country)
    return ", ".join(parts)


def _first_non_empty(*values: Any) -> str:
    for value in values:
        if isinstance(value, str):
            stripped = value.strip()
            if stripped:
                return stripped
    return ""


def _map_remote(location_type: Any) -> Optional[bool]:
    if location_type in ("1", 1):
        return True
    if location_type in ("0", 0):
        return False
    if location_type in ("2", 2):
        return None
    return None


def _extract_compensation(compensation: Any) -> Tuple[Optional[float], Optional[float]]:
    if not isinstance(compensation, dict):
        return None, None

    min_raw = None
    max_raw = None

    if isinstance(compensation.get("range"), dict):
        range_dict = compensation["range"]
        min_raw = range_dict.get("min") or range_dict.get("minimum")
        max_raw = range_dict.get("max") or range_dict.get("maximum")
    else:
        min_raw = compensation.get("min") or compensation.get("minimum")
        max_raw = compensation.get("max") or compensation.get("maximum")

    return _coerce_number(min_raw), _coerce_number(max_raw)


def _coerce_number(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.strip().replace(",", "")
        if not cleaned:
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


__all__ = ["BambooHRParser", "is_bamboohr_job_url"]
