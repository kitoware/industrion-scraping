from __future__ import annotations

import re
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlparse


def extract_anchors_from_page_data(page_data: Dict[str, Any]) -> List[Dict[str, str]]:
    links = page_data.get("links") or []
    anchors: List[Dict[str, str]] = []
    for link in links:
        href = link.get("href") or link.get("url") or ""
        text = link.get("text") or link.get("label") or ""
        if href:
            anchors.append({"href": href, "text": text})
    return anchors


def absolutize_and_dedupe_urls(urls: List[str], base_url: str) -> List[str]:
    deduped: List[str] = []
    seen = set()
    for u in urls:
        if not u:
            continue
        # Skip fragment-only anchors (e.g., "#section")
        if u.lstrip().startswith("#"):
            continue
        abs_u = urljoin(base_url, u)
        parsed = urlparse(abs_u)
        scheme = (parsed.scheme or "").lower()
        if scheme not in ("http", "https"):
            continue
        if abs_u not in seen:
            seen.add(abs_u)
            deduped.append(abs_u)
    return deduped


def normalize_job_type(value: str) -> Optional[str]:
    if not value:
        return None
    v = value.strip().lower()
    if any(s in v for s in ["full-time", "full time", "permanent"]):
        return "Full Time"
    if any(s in v for s in ["part-time", "part time"]):
        return "Part Time"
    if any(s in v for s in ["intern", "co-op", "internship"]):
        return "Internship"
    return None


def detect_remote_from_text(text: str) -> bool:
    if not text:
        return False
    pattern = re.compile(r"\b(remote|work from anywhere|wfh|hybrid)\b", re.IGNORECASE)
    return bool(pattern.search(text))


def sanitize_application_link(link: Optional[str], job_url: str, canonical_url: str) -> str:
    """Ensure application link is usable by falling back to job URL when invalid."""
    candidate = (link or "").strip()
    if candidate:
        parsed = urlparse(candidate)
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            return candidate
        if parsed.scheme == "mailto":
            return candidate
        if not parsed.scheme:
            base = canonical_url or job_url
            if base:
                return urljoin(base, candidate)
            return candidate
    # Fallbacks
    return canonical_url or job_url


def postprocess_fields(fields: Dict[str, Any], company_override: Optional[str], page_html: str, job_url: str = "", canonical_url: str = "") -> Dict[str, Any]:
    # Remote OK fallback
    if fields.get("remote_ok") is None:
        fields["remote_ok"] = detect_remote_from_text(page_html)

    # Company override
    if company_override:
        fields["company_name"] = company_override

    # Job type normalization
    jt = normalize_job_type(fields.get("job_type", ""))
    if jt:
        fields["job_type"] = jt

    fields["application_link"] = sanitize_application_link(
        fields.get("application_link"),
        job_url,
        canonical_url,
    )

    # Salaries -> leave None if missing; handled in row conversion
    return fields


def to_sheet_row(fields: Dict[str, Any]) -> List[Any]:
    return [
        fields.get("title", ""),
        fields.get("company_name", ""),
        fields.get("location", ""),
        "TRUE" if bool(fields.get("remote_ok")) else "FALSE",
        fields.get("job_type", ""),
        fields.get("description_html", ""),
        "" if fields.get("min_salary") in (None, "") else fields.get("min_salary"),
        "" if fields.get("max_salary") in (None, "") else fields.get("max_salary"),
        fields.get("application_link", ""),
    ]


