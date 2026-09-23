"""Download verification: content-type, magic bytes, completeness, checksum.

A downloaded object is valid only if every applicable layer passes. An HTML
error page is never allowed to become `video.mp4`.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path

# (name, offset, signature bytes) — ordered list; a file may match several.
MAGIC_SIGNATURES: list[tuple[str, int, bytes]] = [
    ("mp4/m4a/mov/m4v (ftyp)", 4, b"ftyp"),
    ("mkv/webm (EBML)", 0, b"\x1a\x45\xdf\xa3"),
    ("avi (RIFF-AVI)", 8, b"AVI "),
    ("flv", 0, b"FLV"),
    ("mpeg-ps", 0, b"\x00\x00\x01\xba"),
    ("mpeg-ts", 0, b"\x47"),
    ("mp3 (ID3)", 0, b"ID3"),
    ("mp3 (sync)", 0, b"\xff\xfb"),
    ("wav (RIFF-WAVE)", 8, b"WAVE"),
    ("flac", 0, b"fLaC"),
    ("ogg", 0, b"OggS"),
]

HTML_MARKERS = (b"<!doctype html", b"<html", b"<head", b"<body", b"\x1f\x8b" )


@dataclass
class VerificationResult:
    ok: bool
    sniffed_containers: list[str] = field(default_factory=list)
    checks: dict[str, str] = field(default_factory=dict)   # layer -> "pass"/"fail: reason"
    reasons: list[str] = field(default_factory=list)


def _magic(path: Path) -> bytes:
    with open(path, "rb") as fh:
        return fh.read(64)


def sniff_magic(path: Path) -> list[str]:
    """Return container names whose signature matches the file head."""
    head = _magic(path)
    found: list[str] = []
    for name, offset, sig in MAGIC_SIGNATURES:
        if len(head) >= offset + len(sig) and head[offset:offset + len(sig)] == sig:
            found.append(name)
    return found


def looks_like_html(head: bytes, content_type: str | None = None) -> bool:
    """Detect HTML/error pages masquerading as media.

    Checks declared content type and the body itself — some servers lie about
    Content-Type, so the body check is the authoritative one.
    """
    if content_type and content_type.split(";")[0].strip().lower().startswith("text/"):
        return True
    low = head[:512].lower()
    return any(low.startswith(m) or low.lstrip()[:15].startswith(m)
               for m in HTML_MARKERS[:-1])


def sha256_file(path: Path, chunk_size: int = 1 << 20) -> str:
    """Streaming sha256 — safe for 10 GB files (no read_all)."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(chunk_size):
            h.update(chunk)
    return h.hexdigest()


def verify_download(
    path: Path,
    content_type: str | None = None,
    content_length: int | None = None,
    min_size: int = 1024,
    expected_ext: str | None = None,
) -> VerificationResult:
    """Run the verification chain over a completed download.

    Layers: size floor, HTML detection, magic bytes, extension sanity,
    Content-Length completeness (when known).
    """
    checks: dict[str, str] = {}
    reasons: list[str] = []          # hard failures only
    size = path.stat().st_size

    # 1. size floor
    if size < min_size:
        checks["size"] = f"fail: {size}B < {min_size}B floor"
        reasons.append(f"File too small to be media ({size} bytes).")
    else:
        checks["size"] = "pass"

    # 2. HTML / error-page detection (read head once)
    head = _magic(path)
    if looks_like_html(head, content_type):
        checks["html"] = "fail: response is HTML/text, not binary media"
        reasons.append(
            "Downloaded content is an HTML/text page (interstitial, error or "
            "login page) — not media. The source adapter must handle the "
            "confirmation flow before downloading."
        )
    else:
        checks["html"] = "pass"

    # 3. magic bytes
    sniffed = sniff_magic(path)
    if sniffed:
        checks["magic"] = f"pass: {', '.join(sniffed)}"
    else:
        checks["magic"] = "fail: no known container signature"
        reasons.append(
            "No known container signature (mp4/mkv/mov/webm/mp3/wav/flac/ogg/…). "
            "The file may be encrypted, truncated, or an unsupported wrapper."
        )

    # 4. extension sanity (warning only — never fails verification)
    if expected_ext:
        actual = path.suffix.lstrip(".").lower()
        if actual and actual != expected_ext.lstrip(".").lower():
            checks["extension"] = f"warn: expected .{expected_ext}, got .{actual}"
        else:
            checks["extension"] = "pass"

    # 5. completeness against Content-Length
    if content_length is not None:
        if size >= content_length:
            checks["completeness"] = f"pass: {size}/{content_length} bytes"
        else:
            checks["completeness"] = f"fail: {size}/{content_length} bytes"
            reasons.append(
                f"Incomplete download: {size} of {content_length} bytes "
                f"({100.0 * size / max(content_length, 1):.1f}%)."
            )
    else:
        checks["completeness"] = "unknown: server sent no Content-Length"

    return VerificationResult(
        ok=not reasons,
        sniffed_containers=sniffed,
        checks=checks,
        reasons=reasons,
    )


def quarantine(path: Path, reason: str) -> Path:
    """Move a failed download aside (never silently delete uncertain content)."""
    qdir = path.parent / "_quarantine"
    qdir.mkdir(parents=True, exist_ok=True)
    target = qdir / path.name
    os.replace(path, target)
    (qdir / (path.name + ".reason.txt")).write_text(reason, encoding="utf-8")
    return target
