"""Small, provider-agnostic helpers for the direct Earth Health builders.

The direct builders deliberately keep downloads outside the public deployment tree.  This
module only handles resumable transport, public metadata parsing, and the
authentication error that NASA's Earthdata Login endpoints return before a
credential is configured.
"""

from __future__ import annotations

from html.parser import HTMLParser
import json
import os
from pathlib import Path
import time
from typing import Iterable
from urllib.parse import unquote


class ProviderAccessError(RuntimeError):
    """A provider request failed before a data file could be staged."""


class EarthdataAuthenticationRequired(ProviderAccessError):
    """GES DISC returned its Earthdata Login redirect/unauthorized response."""


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        href = dict(attrs).get("href")
        if href:
            self.hrefs.append(unquote(href))


def html_links(text: str) -> list[str]:
    parser = LinkParser()
    parser.feed(text)
    return parser.hrefs


def write_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.part")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)
    return path


def read_bearer_token(token_file: Path | None = None) -> str | None:
    token = os.environ.get("EARTHDATA_TOKEN", "").strip()
    if token:
        return token
    if token_file:
        token = token_file.read_text(encoding="utf-8").strip()
        if token:
            return token
    return None


def earthdata_headers(token: str | None) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"} if token else {}


def is_earthdata_login_redirect(location: str | None) -> bool:
    """Recognize both absolute and GES DISC's relative login redirects."""

    normalized = (location or "").lower()
    return "urs.earthdata.nasa.gov" in normalized or "/login/urs" in normalized


def auth_error_message(url: str, status: int | None = None, location: str | None = None) -> str:
    status_text = f"HTTP {status}" if status else "an Earthdata Login redirect"
    location_text = f" ({location})" if location else ""
    return (
        f"NASA GES DISC direct access returned {status_text}{location_text}. "
        "Create/sign in to a NASA Earthdata Login account, authorize the GES DISC application, "
        "generate a user token at https://urs.earthdata.nasa.gov, then rerun with "
        "EARTHDATA_TOKEN set (or --earthdata-token-file pointing to an F: token file)."
    )


def raise_for_provider_response(response, url: str) -> None:
    location = response.headers.get("Location", "")
    if response.status_code in {401, 403} or is_earthdata_login_redirect(location):
        raise EarthdataAuthenticationRequired(auth_error_message(url, response.status_code, location))
    if not response.ok:
        raise ProviderAccessError(f"Provider request failed with HTTP {response.status_code}: {url}")


def download_resumable(
    session,
    url: str,
    destination: Path,
    *,
    headers: dict[str, str] | None = None,
    timeout: int = 180,
    expected_size: int | None = None,
    retries: int = 3,
    chunk_size: int = 1024 * 1024,
) -> Path:
    """Download one file with a sidecar checkpoint and an atomic final rename."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and destination.stat().st_size > 0:
        if expected_size is None or destination.stat().st_size == expected_size:
            return destination

    partial = destination.with_name(f".{destination.name}.part")
    for attempt in range(retries):
        existing = partial.stat().st_size if partial.exists() else 0
        request_headers = dict(headers or {})
        if existing:
            request_headers["Range"] = f"bytes={existing}-"
        try:
            response = session.get(
                url,
                headers=request_headers,
                stream=True,
                allow_redirects=False,
                timeout=timeout,
            )
            raise_for_provider_response(response, url)
            append = existing > 0 and response.status_code == 206
            if existing and not append:
                existing = 0
            mode = "ab" if append else "wb"
            with partial.open(mode) as stream:
                for block in response.iter_content(chunk_size=chunk_size):
                    if block:
                        stream.write(block)
            response.close()
            final_size = partial.stat().st_size
            content_range = response.headers.get("Content-Range", "")
            if expected_size is None and "/" in content_range:
                try:
                    expected_size = int(content_range.rsplit("/", 1)[1])
                except ValueError:
                    pass
            if expected_size is not None and final_size != expected_size:
                raise ProviderAccessError(
                    f"incomplete download for {url}: got {final_size} bytes, expected {expected_size}"
                )
            partial.replace(destination)
            return destination
        except EarthdataAuthenticationRequired:
            raise
        except Exception:
            if attempt + 1 >= retries:
                raise
            time.sleep(min(2 ** attempt, 8))
    raise AssertionError("unreachable")


def output_family_path(output_root: Path, family_id: str) -> Path:
    return output_root / "frontend" / "public" / "data" / "indices" / "families" / f"{family_id}.index.json"


def as_jsonable(value):
    """Convert common NumPy scalar values without importing NumPy at module load."""

    if hasattr(value, "item"):
        return value.item()
    return value


def date_range_years(start_year: int, end_year: int) -> Iterable[int]:
    if end_year < start_year:
        raise ValueError(f"end year {end_year} precedes start year {start_year}")
    return range(start_year, end_year + 1)
