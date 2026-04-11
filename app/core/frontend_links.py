from __future__ import annotations

from typing import Mapping, Any, Optional
from urllib.parse import urlencode

from app.core.config import settings


def get_frontend_base_url() -> str:
    return (settings.FRONTEND_URL or "http://192.168.1.167:3033").rstrip("/")


def build_frontend_url(
    path: str = "",
    *,
    query: Optional[Mapping[str, Any]] = None,
    fragment: Optional[str] = None,
) -> str:
    base = get_frontend_base_url()
    normalized_path = ""
    if path:
        normalized_path = path if path.startswith("/") else f"/{path}"

    url = f"{base}{normalized_path}"
    if query:
        filtered_query = {
            key: value
            for key, value in query.items()
            if value is not None and value != ""
        }
        if filtered_query:
            url = f"{url}?{urlencode(filtered_query, doseq=True)}"

    if fragment:
        url = f"{url}#{fragment.lstrip('#')}"

    return url

