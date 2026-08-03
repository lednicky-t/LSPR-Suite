from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

GITHUB_REPO = "lednicky-t/LSPR-Suite"
LATEST_RELEASE_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
ASSET_NAME_SUFFIX = ".zip"
REQUEST_TIMEOUT_S = 8.0
DOWNLOAD_TIMEOUT_S = 60.0
DOWNLOAD_CHUNK_SIZE = 1 << 16


@dataclass(frozen=True)
class ReleaseInfo:
    tag: str
    version: tuple[int, ...]
    notes: str
    download_url: str
    asset_name: str


def parse_version(tag: str) -> tuple[int, ...]:
    """Turn a tag like "v1.4.0" (or a bare "1.4.0") into (1, 4, 0) for comparison."""
    text = tag.strip()
    if text[:1] in ("v", "V"):
        text = text[1:]
    parts: list[int] = []
    for chunk in text.split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts) or (0,)


def is_newer(current: str, latest: str) -> bool:
    return parse_version(latest) > parse_version(current)


def _parse_release_payload(payload: dict) -> ReleaseInfo:
    assets = payload.get("assets") or []
    zip_asset = next(
        (asset for asset in assets if str(asset.get("name", "")).endswith(ASSET_NAME_SUFFIX)),
        None,
    )
    if zip_asset is None:
        raise LookupError("The latest release has no portable-bundle zip attached.")
    tag = str(payload.get("tag_name", "")).strip()
    if not tag:
        raise LookupError("The latest release has no tag name.")
    return ReleaseInfo(
        tag=tag,
        version=parse_version(tag),
        notes=str(payload.get("body", "") or "").strip(),
        download_url=str(zip_asset["browser_download_url"]),
        asset_name=str(zip_asset.get("name", "")),
    )


def fetch_latest_release(timeout: float = REQUEST_TIMEOUT_S) -> ReleaseInfo:
    """Fetch the newest published (non-draft, non-prerelease) GitHub release.

    Raises urllib.error.URLError / LookupError / ValueError on failure - callers
    are expected to catch these and show a friendly message rather than crash.
    """
    request = urllib.request.Request(
        LATEST_RELEASE_URL,
        headers={"Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return _parse_release_payload(payload)


def download_release(
    info: ReleaseInfo,
    dest_path: Path,
    on_progress: Callable[[int], None] | None = None,
    timeout: float = DOWNLOAD_TIMEOUT_S,
) -> Path:
    """Stream the release's zip asset to *dest_path*, reporting 0-100 progress."""
    request = urllib.request.Request(info.download_url, headers={"Accept": "application/octet-stream"})
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        total = response.length or int(response.headers.get("Content-Length", 0) or 0)
        written = 0
        with dest_path.open("wb") as handle:
            while True:
                chunk = response.read(DOWNLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                handle.write(chunk)
                written += len(chunk)
                if on_progress is not None and total > 0:
                    on_progress(min(int(written * 100 / total), 100))
    return dest_path
