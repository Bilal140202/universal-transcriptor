"""MediaSourceResolver — URL classification and adapter dispatch.

USER URL → SOURCE CLASSIFICATION → DOMAIN DETECTION → SOURCE ADAPTER
→ ACCESS STRATEGY → DOWNLOAD → VERIFY → MEDIA PROBE → LOCAL OBJECT
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from .base import MediaObject, SourceAdapter, SourceNotSupported, VerificationFailed
from .gdrive import GoogleDriveAdapter
from .http import DirectHTTPAdapter


class MediaSourceResolver:
    """Registry-driven source resolution. New sources plug in via `register`."""

    def __init__(self, adapters: list[SourceAdapter] | None = None) -> None:
        self._adapters: list[SourceAdapter] = []
        for adapter in (adapters or [GoogleDriveAdapter(), DirectHTTPAdapter(),
                                     YouTubeAdapter()]):
            self.register(adapter)

    def register(self, adapter: SourceAdapter) -> None:
        self._adapters.append(adapter)
        self._adapters.sort(key=lambda a: a.priority)

    def classify(self, url: str) -> SourceAdapter:
        for adapter in self._adapters:
            if adapter.can_handle(url):
                return adapter
        raise SourceNotSupported(
            f"No registered adapter handles {url!r}. Supported: "
            + ", ".join(sorted(a.name for a in self._adapters))
            + ". Implement a SourceAdapter to add new sources."
        )

    def resolve(self, url: str, dest_dir: Path, **opts) -> MediaObject:
        adapter = self.classify(url)
        return adapter.resolve(url, dest_dir, **opts)

    def list_adapters(self) -> list[str]:
        return [a.name for a in self._adapters]


class YouTubeAdapter(SourceAdapter):
    """YouTube adapter — delegates to yt-dlp when available (see docs/RESEARCH.md).

    The bypass machinery proven in Bilal140202/ytagent stays out of this engine;
    we simply shell out to yt-dlp, which the ytagent project wraps in a robust
    13-method fallback chain for harder environments.
    """

    name = "youtube"
    priority = 30

    def can_handle(self, url: str) -> bool:
        host = urlparse(url).netloc.lower()
        return host.endswith(("youtube.com", "youtu.be", "youtube-nocookie.com"))

    def resolve(self, url: str, dest_dir: Path, **opts) -> MediaObject:
        import shutil
        import subprocess

        if not shutil.which("yt-dlp"):
            raise SourceNotSupported(
                "YouTube acquisition requires yt-dlp on PATH "
                "(pip install yt-dlp). For datacenter-IP environments, see "
                "github.com/Bilal140202/ytagent for a hardened fallback chain."
            )
        dest_dir.mkdir(parents=True, exist_ok=True)
        cmd = ["yt-dlp", "-o", str(dest_dir / "%(id)s.%(ext)s"),
               "--no-playlist", url]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=7200)
        if proc.returncode != 0:
            raise VerificationFailed(f"yt-dlp failed: {proc.stderr[-500:]}")
        produced = sorted(dest_dir.glob("*.*"), key=lambda p: p.stat().st_mtime)
        media = [p for p in produced if p.suffix.lower().lstrip(".") in
                 ("mp4", "mkv", "webm", "m4a", "mp3")]
        if not media:
            raise VerificationFailed("yt-dlp produced no recognizable media file.")
        from ..models import AcquisitionRecord
        rec = AcquisitionRecord(source_url=url, source_type=self.name,
                                file_name=media[-1].name,
                                size_bytes=media[-1].stat().st_size, success=True)
        return MediaObject(path=media[-1], record=rec)
