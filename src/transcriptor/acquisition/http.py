"""DirectHTTPAdapter — streaming, resumable, verified HTTP(S) downloads.

Large-file contract (spec §4.3): chunked streaming to disk, HTTP Range resume,
Content-Length completeness checking, early HTML rejection. Never `read_all()`.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from ..models import AcquisitionRecord
from .base import AccessDenied, MediaObject, SourceAdapter, VerificationFailed
from .verify import quarantine, sha256_file, verify_download

CHUNK_SIZE = 4 << 20  # 4 MiB
USER_AGENT = "universal-transcriptor/0.1 (+media acquisition engine)"


class _Throttled(Exception):
    """Internal: source served a transient throttle/consent page."""


_AUTH_MARKERS = ("you need access", "request access", "sign in to continue",
                 "doesn't have access", "ask the file owner")


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

    # -- ranged chunked download core ---------------------------------------------

    def ranged_download(
        self,
        url: str,
        dest_dir: Path,
        file_name: str | None = None,
        expected_ext: str | None = None,
        resume: bool = True,
        max_bytes: int = 32 << 30,
        chunk_bytes: int = 16 << 20,
        pacing_seconds: float | None = None,
        params: dict | None = None,
        record: AcquisitionRecord | None = None,
        **session_kwargs,
    ) -> MediaObject:
        """Acquire media in HTTP Range chunks (RFC 7233) — streaming to disk.

        Rationale (validated against Google Drive, 2026-09): sources that gate
        *full* GETs behind a "too many users" quota interstitial still honor
        ranged requests with real media (206). Downloading in verified chunks
        is standard HTTP and both robust (each chunk restartable) and
        memory-safe (fixed-size buffers, never read_all).

        Protocol notes from validation:
        - The confirm-flow parameters may be single-use per *full* GET; the
          FIRST ranged request here therefore doubles as the probe AND the
          first chunk — no separate HEAD/0-0 pre-flight that could consume it.
        - If the server answers 200 (Range ignored), that single response body
          IS the whole file: stream it once and verify — never re-request.
        - Repeated ranged requests with the same parameters are honored.
        """
        rec = record or AcquisitionRecord(source_url=url, source_type=self.name)
        rec.started_at = rec.started_at or _now()
        dest_dir.mkdir(parents=True, exist_ok=True)
        session = _RetrySession()
        headers = dict(session_kwargs.pop("headers", {}))
        if pacing_seconds is None:
            pacing_seconds = float(os.environ.get("TRANSCRIPTOR_CHUNK_PACING_S", "2"))

        def _part_path(name: str) -> Path:
            target = dest_dir / name
            return target.with_suffix(target.suffix + ".part")

        target_name = file_name
        partial: Path | None = None
        if target_name is not None:
            partial = _part_path(target_name)

        total: int | None = None            # from Content-Range when ranged
        ctype: str | None = None
        started = time.monotonic()
        attempts = 0
        offset = (partial.stat().st_size
                  if (resume and partial is not None and partial.exists())
                  else 0)

        while True:
            done = total is not None and offset >= total
            if done:
                break
            end = (offset + chunk_bytes - 1) if total is None \
                else min(offset + chunk_bytes, total) - 1
            try:
                with session.get(url, params=params, stream=True,
                                 timeout=(15, 120), allow_redirects=True,
                                 headers={**headers,
                                          "Range": f"bytes={offset}-{end}"}) as r:
                    status = r.status_code
                    this_ctype = r.headers.get("Content-Type", ctype or "")
                    body_is_text = this_ctype.split(";")[0].strip().lower() \
                        .startswith("text/")

                    if status == 416:
                        if total is not None and offset >= total:
                            break
                        raise VerificationFailed(
                            "HTTP 416 on a non-final ranged request "
                            f"(offset={offset}, total={total}).")
                    if status not in (200, 206):
                        raise VerificationFailed(
                            f"Chunk request bytes={offset}-{end} answered "
                            f"HTTP {status}.")

                    if body_is_text:
                        # Google Drive (validated 2026-09) serves a quota/
                        # consent HTML page for a PROBABILISTIC subset of
                        # requests to popular files — even valid ranged
                        # requests. Transient by observation → retry with
                        # backoff. Permanent access denials fail fast.
                        low = (r.text or "").lower()
                        if any(m in low for m in _AUTH_MARKERS):
                            raise VerificationFailed(
                                "Source denies access (auth interstitial) — "
                                "the file must be shared 'Anyone with the "
                                "link' or acquired with credentials.")
                        raise _Throttled(
                            f"throttle/consent HTML at offset {offset} "
                            f"(status {status})")

                    cr = r.headers.get("Content-Range", "")
                    if status == 206 and "/" in cr:
                        tail = cr.rsplit("/", 1)[-1]
                        if tail.isdigit():
                            total = int(tail)
                            if total > max_bytes:
                                raise VerificationFailed(
                                    f"Source is {total} bytes; above the "
                                    f"{max_bytes} byte cap.")
                    elif status == 200:
                        # Range ignored: this response body is the ENTIRE
                        # file. Any prior bytes are invalid — restart file.
                        offset = 0
                        total = None
                        clen = r.headers.get("Content-Length", "")
                        if clen.isdigit() and int(clen) > max_bytes:
                            raise VerificationFailed(
                                f"Source is {clen} bytes; above the "
                                f"{max_bytes} byte cap.")
                        # (verify_download re-checks completeness after)

                    # Filename may only be knowable from this response.
                    if target_name is None:
                        cd = r.headers.get("Content-Disposition", "")
                        if "filename=" in cd:
                            name = cd.split("filename=")[-1].strip() \
                                .strip('"').split(";")[0]
                            target_name = Path(name).name if name else None
                        if target_name is None:
                            target_name = self._filename_from_url(
                                str(r.url), expected_ext)
                        partial = dest_dir / target_name
                        partial = partial.with_suffix(
                            partial.suffix + ".part")
                        # First response: honor any resumable progress.
                        if resume and partial.exists() \
                                and partial.stat().st_size > offset:
                            offset = partial.stat().st_size
                    if total is not None and offset >= total:
                        break
                    rec.resolved_url = str(r.url)
                    ctype = this_ctype or ctype
                    rec.content_type = ctype or None

                    mode = "ab" if offset else "wb"
                    written = 0
                    assert partial is not None
                    with open(partial, mode) as fh:
                        for chunk in r.iter_content(chunk_size=CHUNK_SIZE):
                            if chunk:
                                fh.write(chunk)
                                written += len(chunk)
                    offset += written
                    if pacing_seconds and written:
                        time.sleep(pacing_seconds)
                    if status == 200:
                        break  # full body already delivered in this response

                attempts = 0
            except (_Throttled, requests.ConnectionError,
                    requests.Timeout) as exc:
                attempts += 1
                if partial is not None and partial.exists():
                    offset = partial.stat().st_size  # resume from what landed
                if attempts > 12:
                    raise VerificationFailed(
                        f"Ranged download stalled at byte {offset}"
                        f"/{total if total is not None else '?'} after "
                        f"{attempts - 1} retries: {exc}") from exc
                time.sleep(min(30.0, 2.0 ** attempts))
        elapsed = time.monotonic() - started

        if partial is None:
            raise VerificationFailed(
                "No data was written — the source answered every request "
                "with an error page.")

        # -- completeness + integrity ------------------------------------------------
        got = partial.stat().st_size
        if total is not None and got != total:
            reason = (f"Ranged download incomplete: {got}/{total} bytes.")
            quarantine(partial, reason)
            rec.success = False
            rec.error = reason
            raise VerificationFailed(reason)

        result = verify_download(partial, content_type=ctype or "",
                                 content_length=total, expected_ext=expected_ext)
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
    def _filename_from_url(url: str, expected_ext: str | None) -> str:
        name = Path(url.split("?", maxsplit=1)[0]).name or "download.bin"
        if expected_ext and not name.lower().endswith(expected_ext.lower()):
            name += expected_ext if expected_ext.startswith(".") else f".{expected_ext}"
        return name

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
