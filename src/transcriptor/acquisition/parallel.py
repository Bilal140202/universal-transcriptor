"""ParallelRangedDownloader — multi-connection segment acquisition.

Why (validated against Google Drive, 2026-09):
- Popular files behind Drive's quota gate serve a "too many users" HTML page
  for a probabilistic subset of requests, and after a sustained single-stream
  pull (~200 MB observed) the gate becomes persistent for a while.
- Ranged requests are otherwise honored reliably (206 + Content-Range), and
  multiple independent sessions each obtain their own confirmation flow.

Design (classic download-manager architecture):
- The file is split into N contiguous segments; each worker thread owns one
  segment, streams its chunks to its own .part<N> file (append + resume),
  and validates every response's Content-Range start against the requested
  offset (protects against silent Range violations).
- Workers retry transient throttle pages with capped-backoff persistence and
  can refresh their confirm parameters via a source-provided callable.
- Completed segments are concatenated in order into the final target, then
  verified with the same layered checks as the single-stream path.

Memory safety: fixed-size chunk buffers per worker, never read_all.
"""
from __future__ import annotations

import os
import random
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from ..models import AcquisitionRecord
from .base import VerificationFailed
from .http import CHUNK_SIZE, _RetrySession, DirectHTTPAdapter
from .verify import sha256_file, verify_download


class _SegmentDone(Exception):
    """Internal: segment fully downloaded."""


class ParallelRangedDownloader:
    """Split a ranged-capable source into segments and fetch them in parallel."""

    def __init__(
        self,
        http: DirectHTTPAdapter,
        workers: int = 5,
        chunk_bytes: int = 16 << 20,
        max_stall_seconds: float = 3600.0,
        backoff_base: float = 3.0,
        backoff_cap: float = 45.0,
    ) -> None:
        self.http = http
        self.workers = max(1, int(os.environ.get("TRANSCRIPTOR_PARALLEL_WORKERS", workers)))
        self.chunk_bytes = chunk_bytes
        self.max_stall_seconds = max_stall_seconds
        self.backoff_base = backoff_base
        self.backoff_cap = backoff_cap

    # -- public ------------------------------------------------------------------

    def download(
        self,
        url: str,
        dest_dir: Path,
        *,
        params: dict | None = None,
        file_name: str | None = None,
        expected_ext: str | None = None,
        record: AcquisitionRecord | None = None,
        headers: dict | None = None,
        refresh_params=None,           # callable() -> fresh params (e.g. new uuid)
    ):
        rec = record or AcquisitionRecord(source_url=url, source_type="http-parallel")
        rec.started_at = rec.started_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
        dest_dir.mkdir(parents=True, exist_ok=True)
        headers = headers or {}

        # -- probe total size + first segment content --------------------------------
        session = _RetrySession()
        total, ctype, final_url, disp_name = self._probe(
            session, url, params, headers, refresh_params=refresh_params)
        if total is None:
            raise VerificationFailed(
                "Parallel download requires a known total size (Content-Range).")

        if file_name:
            target_name = file_name
        else:
            cd_name = disp_name
            target_name = cd_name or self.http._filename_from_url(final_url, expected_ext)
        target = dest_dir / target_name

        # Deterministic segment bounds (resume-safe across restarts).
        seg_len = (total + self.workers - 1) // self.workers
        bounds = [
            (i * seg_len, min((i + 1) * seg_len, total) - 1)
            for i in range(self.workers)
            if i * seg_len < total
        ]
        part_files = [
            dest_dir / f"{target_name}.part{chr(ord('a') + i)}"
            for i in range(len(bounds))
        ]

        rec.resolved_url = final_url
        rec.content_type = ctype or None
        rec.error = None

        started = time.monotonic()
        failures: list[str] = []

        def worker(idx: int) -> None:
            seg_start, seg_end = bounds[idx]
            part = part_files[idx]
            offset = seg_start + part.stat().st_size if part.exists() else seg_start
            session_w = _RetrySession()
            session_w.headers.update(headers)
            attempts = 0
            last_ok = time.monotonic()
            try:
                while offset <= seg_end:
                    if time.monotonic() - last_ok > self.max_stall_seconds:
                        raise VerificationFailed(
                            f"segment {idx} stalled >{self.max_stall_seconds:.0f}s")
                    end = min(offset + self.chunk_bytes - 1, seg_end)
                    try:
                        req_headers = {**headers, "Range": f"bytes={offset}-{end}"}
                        with session_w.get(url, params=params, stream=True,
                                           timeout=(15, 180), allow_redirects=True,
                                           headers=req_headers) as r:
                            cr = r.headers.get("Content-Range", "")
                            ctype_body = r.headers.get("Content-Type", "")
                            # IMPORTANT: classify the throttle/consent HTML page
                            # FIRST — Google's quota page arrives as a plain
                            # `200 text/html` with no Content-Range, which must
                            # NOT be confused with a genuine Range violation.
                            if ctype_body.split(";")[0].strip().lower().startswith("text/"):
                                low = (r.text or "").lower()
                                for marker in ("you need access", "request access",
                                               "sign in to continue",
                                               "ask the file owner"):
                                    if marker in low:
                                        raise VerificationFailed(
                                            "Source denies access (auth page) — "
                                            "file must be shared publicly.")
                                raise _ThrottledSeg(
                                    f"segment {idx}: throttle/consent HTML")
                            if r.status_code == 200 and not cr:
                                # Genuine Range violation → abort the whole
                                # parallel path (wrong data risk).
                                raise _RangeViolated(
                                    f"segment {idx}: server ignored Range (200).")
                            if r.status_code not in (200, 206):
                                raise _ThrottledSeg(
                                    f"segment {idx}: HTTP {r.status_code}")
                            expected_start = f"bytes {offset}-"
                            if r.status_code == 206:
                                if not cr.startswith(expected_start):
                                    raise _ThrottledSeg(
                                        f"segment {idx}: Content-Range mismatch "
                                        f"({cr[:40]!r} vs {expected_start!r})")

                            written = 0
                            with open(part, "ab" if offset > seg_start else "wb") as fh:
                                for chunk in r.iter_content(chunk_size=CHUNK_SIZE):
                                    if chunk:
                                        fh.write(chunk)
                                        written += len(chunk)
                            offset += written
                            if written:
                                last_ok = time.monotonic()
                                attempts = 0
                            if offset > seg_end:
                                raise _SegmentDone()
                    except (_ThrottledSeg, requests.ConnectionError,
                            requests.Timeout) as exc:
                        attempts += 1
                        if refresh_params is not None and attempts % 4 == 0:
                            try:
                                fresh = refresh_params()
                                if fresh:
                                    params.update(fresh)
                            except Exception:
                                pass
                        time.sleep(min(self.backoff_cap,
                                       self.backoff_base * (1.5 ** min(attempts, 8))
                                       * (0.7 + 0.6 * random.random())))
            except _SegmentDone:
                return
            except _RangeViolated as exc:
                failures.append(str(exc))
            except VerificationFailed as exc:
                failures.append(str(exc))

        threads = [threading.Thread(target=worker, args=(i,), daemon=True)
                   for i in range(len(bounds))]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        elapsed = time.monotonic() - started
        if failures:
            rec.success = False
            rec.error = "; ".join(failures[:3])
            raise VerificationFailed(
                f"Parallel download failed: {rec.error}")

        # -- completeness per segment -------------------------------------------------
        for idx, (seg_start, seg_end) in enumerate(bounds):
            expect = seg_end - seg_start + 1
            got = part_files[idx].stat().st_size if part_files[idx].exists() else 0
            if got != expect:
                raise VerificationFailed(
                    f"segment {idx} incomplete: {got}/{expect} bytes")

        # -- concatenate -----------------------------------------------------------
        with open(target, "wb") as out:
            for part in part_files:
                with open(part, "rb") as fh:
                    while True:
                        buf = fh.read(4 << 20)
                        if not buf:
                            break
                        out.write(buf)
        got_total = target.stat().st_size
        if got_total != total:
            os.unlink(target)
            raise VerificationFailed(
                f"Concatenated size mismatch: {got_total}/{total}")

        result = verify_download(target, content_type=ctype or "",
                                 content_length=total, expected_ext=expected_ext)
        if not result.ok:
            reason = "; ".join(result.reasons)
            os.unlink(target)
            rec.success = False
            rec.error = reason
            rec.verification = result.checks
            raise VerificationFailed(f"Download verification failed: {reason}")

        # cleanup segment parts
        for part in part_files:
            try:
                part.unlink()
            except OSError:
                pass

        rec.file_name = target.name
        rec.size_bytes = got_total
        rec.download_seconds = round(elapsed, 2)
        rec.checksum_sha256 = sha256_file(target)
        rec.verification = result.checks
        rec.success = True
        rec.ended_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        from .base import MediaObject
        return MediaObject(path=target, record=rec, extras={"verification": result.checks})

    # -- internals ------------------------------------------------------------------

    def _probe(self, session, url, params, headers, refresh_params=None):
        """First ranged request doubles as size probe (and honors resume)."""
        headers = dict(headers)
        last_exc: Exception | None = None
        for attempt in range(10):
            try:
                with session.get(url, params=params, stream=True,
                                 timeout=(15, 180), allow_redirects=True,
                                 headers={**headers, "Range": "bytes=0-0"}) as r:
                    ctype = r.headers.get("Content-Type", "")
                    if ctype.split(";")[0].strip().lower().startswith("text/"):
                        last_exc = _ThrottledSeg("probe answered HTML")
                        if refresh_params is not None:
                            try:
                                fresh = refresh_params()
                                if fresh:
                                    params.update(fresh)
                            except Exception:
                                pass
                        time.sleep(min(45, 3 * (1.5 ** attempt)))
                        continue
                    cr = r.headers.get("Content-Range", "")
                    if r.status_code == 206 and "/" in cr:
                        tail = cr.rsplit("/", 1)[-1]
                        total = int(tail) if tail.isdigit() else None
                    else:
                        total = None
                    return total, ctype, str(r.url), None
            except (requests.ConnectionError, requests.Timeout) as exc:
                last_exc = exc
                time.sleep(min(45, 3 * (1.5 ** attempt)))
        raise VerificationFailed(f"Parallel probe failed: {last_exc}")


class _ThrottledSeg(Exception):
    pass


class _RangeViolated(Exception):
    pass
