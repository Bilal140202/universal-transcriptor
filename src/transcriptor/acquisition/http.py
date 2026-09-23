"""DirectHTTPAdapter — streaming, resumable, verified HTTP(S) downloads.

Large-file contract (spec §4.3): chunked streaming to disk, HTTP Range resume,
Content-Length completeness checking, early HTML rejection. Never `read_all()`.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from ..models import AcquisitionRecord
from .base import AccessDenied, MediaObject, SourceAdapter, VerificationFailed
from .verify import quarantine, sha256_file, verify_download

CHUNK_SIZE = 4 << 20  # 4 MiB
USER_AGENT = "universal-transcriptor/0.1 (+media acquisition engine)"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class _RetrySession(requests.Session):
    """Session with bounded retries on connection/reset errors."""

    def __init__(self, retries: int = 3):
        super().__init__()
        self.retries = retries
        self.headers["User-Agent"] = USER_AGENT

    def request(self, *args, **kwargs):  # type: ignore[override]
        last_exc: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                return super().request(*args, **kwargs)
            except (requests.ConnectionError, requests.Timeout) as exc:
                last_exc = exc
                time.sleep(1.5 ** attempt)
        raise last_exc  # type: ignore[misc]


class DirectHTTPAdapter(SourceAdapter):
    """Adapter for direct media URLs. Also the streaming core other adapters reuse."""

    name = "http"
    priority = 50

    def can_handle(self, url: str) -> bool:
        return url.lower().startswith(("http://", "https://"))

    def resolve(self, url: str, dest_dir: Path, **opts) -> MediaObject:
        """Adapter entry point — delegates to the streaming core."""
        return self.stream_download(url, dest_dir, **opts)

    # -- streaming download core -------------------------------------------------

    def stream_download(
        self,
        url: str,
        dest_dir: Path,
        file_name: str | None = None,
        expected_ext: str | None = None,
        resume: bool = True,
        max_bytes: int = 32 << 30,
        params: dict | None = None,
        record: AcquisitionRecord | None = None,
        **session_kwargs,
    ) -> MediaObject:
        rec = record or AcquisitionRecord(source_url=url, source_type=self.name)
        rec.started_at = rec.started_at or _now()
        dest_dir.mkdir(parents=True, exist_ok=True)

        session = _RetrySession()
        with session.get(url, params=params, stream=True, timeout=(15, 60),
                         allow_redirects=True, **session_kwargs) as resp:
            if resp.status_code in (401, 403):
                raise AccessDenied(f"HTTP {resp.status_code} — access denied for {url}")
            if resp.status_code == 404:
                raise VerificationFailed(f"HTTP 404 — source not found: {url}")
            resp.raise_for_status()

            ctype = resp.headers.get("Content-Type", "")
            clen = resp.headers.get("Content-Length")
            clen = int(clen) if clen and clen.isdigit() else None
            rec.resolved_url = str(resp.url)
            rec.content_type = ctype or None

            if clen and clen > max_bytes:
                raise VerificationFailed(
                    f"Source is {clen} bytes; above the {max_bytes} byte safety cap."
                )

            target = dest_dir / (file_name or self._filename_from(resp, url, expected_ext))
            partial = target.with_suffix(target.suffix + ".part")

            # Range resume: continue an interrupted .part file.
            offset = partial.stat().st_size if (resume and partial.exists()) else 0
            headers = {}
            if offset:
                headers["Range"] = f"bytes={offset}-"
                if clen is not None and offset >= clen:
                    offset = 0  # stale part; restart
                    headers.pop("Range")

            mode = "ab" if offset else "wb"
            started = time.monotonic()
            with open(partial, mode) as fh:
                for chunk in resp.iter_content(chunk_size=CHUNK_SIZE):
                    if chunk:  # keep-alive empty chunks
                        fh.write(chunk)
            elapsed = time.monotonic() - started

            # Completeness / integrity checks BEFORE the file is presented as media.
            result = verify_download(partial, content_type=ctype, content_length=clen,
                                     expected_ext=expected_ext)
            if not result.ok:
                reason = "; ".join(result.reasons)
                quarantine(partial, reason)
                rec.success = False
                rec.error = reason
                rec.verification = result.checks
                raise VerificationFailed(f"Download verification failed: {reason}")

            partial.replace(target)

        rec.file_name = target.name
        rec.size_bytes = target.stat().st_size
        rec.download_seconds = round(elapsed, 2)
        rec.checksum_sha256 = sha256_file(target)
        rec.verification = result.checks
        rec.success = True
        rec.ended_at = _now()
        return MediaObject(path=target, record=rec, extras={"verification": result.checks})

    @staticmethod
    def _filename_from(resp: requests.Response, url: str, expected_ext: str | None) -> str:
        cd = resp.headers.get("Content-Disposition", "")
        if "filename=" in cd:
            name = cd.split("filename=")[-1].strip().strip('"').split(";")[0]
            if name:
                return Path(name).name
        name = Path(url.split("?", maxsplit=1)[0]).name or "download.bin"
        if expected_ext and not name.lower().endswith(expected_ext.lower()):
            name += expected_ext if expected_ext.startswith(".") else f".{expected_ext}"
        return name
