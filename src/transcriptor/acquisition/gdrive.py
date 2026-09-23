"""GoogleDriveAdapter — acquires the actual media behind Drive share links.

Validated mechanics (see docs/RESEARCH.md §1):

1. ``GET https://drive.usercontent.google.com/download?id=<ID>&export=download``
   often answers HTTP 200 with ``text/html`` — a virus-scan confirmation page.
2. The page is a form whose hidden fields (``id``, ``export``, ``confirm``,
   ``uuid``) must be re-submitted to stream the real file.
3. Quota/permission failures also arrive as HTML — they must be detected and
   reported as structured errors, never saved as media.

This adapter reuses DirectHTTPAdapter's streaming core for the final media
transfer (Range resume, completeness, checksums).
"""
from __future__ import annotations

import re
from html import unescape
from pathlib import Path
from urllib.parse import urlparse

from ..models import AcquisitionRecord
from .base import AccessDenied, MediaObject, SourceAdapter, VerificationFailed
from .http import DirectHTTPAdapter, _RetrySession

_FILE_ID_PATTERNS = [
    re.compile(r"/file/d/([A-Za-z0-9_-]{10,})"),
    re.compile(r"/open\?id=([A-Za-z0-9_-]{10,})"),
    re.compile(r"[?&]id=([A-Za-z0-9_-]{10,})"),
    re.compile(r"/d/([A-Za-z0-9_-]{10,})"),
]

_QUOTA_MARKERS = ("download quota", "too many users", "quota exceeded",
                  "unable to download", "no longer available")
_AUTH_MARKERS = ("you need access", "request access", "sign in to continue",
                 "permission", "doesn't have access", "ask the file owner")

_FORM_RE = re.compile(r'<form[^>]*action="([^"]+)"', re.IGNORECASE)
_INPUT_RE = re.compile(
    r'<input[^>]+type=["\']hidden["\'][^>]*>', re.IGNORECASE
)
_NAME_RE = re.compile(r'name=["\']([^"\']+)["\']')
_VALUE_RE = re.compile(r'value=["\']([^"\']*)["\']')


def extract_file_id(url: str) -> str:
    """Extract the Drive file ID from any common share-URL shape."""
    for pattern in _FILE_ID_PATTERNS:
        m = pattern.search(url)
        if m:
            return m.group(1)
    raise VerificationFailed(
        f"Not a recognizable Google Drive file URL: {url!r} "
        "(expected /file/d/<id>/, uc?id=, open?id= or ?id= forms)."
    )


def parse_confirm_form(html: str) -> tuple[str, dict[str, str]]:
    """Parse the interstitial form: (action URL, hidden field params).

    Generic on purpose — Google's parameter names may change; we replay
    whatever the form actually says instead of hard-coding it.
    """
    action_m = _FORM_RE.search(html)
    action = unescape(action_m.group(1)) if action_m else ""
    params: dict[str, str] = {}
    for tag in _INPUT_RE.findall(html):
        nm = _NAME_RE.search(tag)
        if not nm:
            continue
        vm = _VALUE_RE.search(tag)
        params[unescape(nm.group(1))] = unescape(vm.group(1)) if vm else ""
    if not action and not params:
        raise VerificationFailed(
            "Drive returned an HTML page without a downloadable confirmation "
            "form — file may be private, deleted, or quota-limited."
        )
    return action, params


class GoogleDriveAdapter(SourceAdapter):
    """Adapter for drive.google.com share links (public / share-link files)."""

    name = "gdrive"
    priority = 20

    def __init__(self) -> None:
        self._http = DirectHTTPAdapter()

    def can_handle(self, url: str) -> bool:
        host = urlparse(url).netloc.lower()
        return host in ("drive.google.com", "docs.google.com",
                        "drive.usercontent.google.com")

    def resolve(self, url: str, dest_dir: Path, **opts) -> MediaObject:
        file_id = extract_file_id(url)
        rec = AcquisitionRecord(source_url=url, source_type=self.name)
        session = _RetrySession()
        action: str | None = None
        params: dict[str, str]
        html = ""

        # Step 1: request the download endpoint and inspect what comes back.
        probe_url = ("https://drive.usercontent.google.com/download"
                     f"?id={file_id}&export=download")
        with session.get(probe_url, stream=True, timeout=(15, 60)) as resp:
            ctype = resp.headers.get("Content-Type", "")
            if ctype.split(";")[0].strip().lower().startswith("text/"):
                html = resp.text
                self._detect_html_errors(html, file_id)
                action, params = parse_confirm_form(html)
            else:
                # Direct stream (small public files skip the interstitial).
                params = {"id": file_id, "export": "download"}

        # Step 2: replay the confirmation form to stream the actual media.
        action = action or "https://drive.usercontent.google.com/download"
        name_hint = self._filename_from_interstitial(html) or None
        return self._http.stream_download(
            action,
            dest_dir,
            params=params,
            file_name=name_hint,
            expected_ext=None,
            record=rec,
        )

    # -- helpers ---------------------------------------------------------------

    @staticmethod
    def _detect_html_errors(html: str, file_id: str) -> None:
        low = html.lower()
        for marker in _QUOTA_MARKERS:
            if marker in low:
                raise AccessDenied(
                    f"Google Drive quota limit reached for file {file_id} "
                    f"(matched marker {marker!r}). Retry later or use a "
                    "service account / Drive API key."
                )
        for marker in _AUTH_MARKERS:
            if marker in low:
                raise AccessDenied(
                    f"Google Drive denies access to file {file_id} "
                    f"(matched marker {marker!r}). The file must be shared "
                    "'Anyone with the link' or acquired with credentials."
                )

    @staticmethod
    def _filename_from_interstitial(html: str) -> str | None:
        m = re.search(r'uc-name-size[^>]*>.{0,80}?<a[^>]*>([^<]+)</a>', html, re.DOTALL)
        if m:
            name = unescape(m.group(1)).strip()
            return re.sub(r"[\\/:*?\"<>|]", "_", name) or None
        return None
