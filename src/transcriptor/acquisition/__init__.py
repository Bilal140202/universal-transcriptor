"""Acquisition subpackage — MediaSourceResolver and source adapters.

Standalone by design: no imports from ASR/translation modules, so this package
can be published independently as a `media-agent` library.
"""
from .base import (
                   AccessDenied,
                   AcquisitionError,
                   MediaObject,
                   SourceAdapter,
                   SourceNotSupported,
                   VerificationFailed,
)
from .resolver import MediaSourceResolver
from .verify import VerificationResult, sha256_file, sniff_magic, verify_download

__all__ = [
                   "AccessDenied",
                   "AcquisitionError",
                   "MediaObject",
                   "MediaSourceResolver",
                   "SourceAdapter",
                   "SourceNotSupported",
                   "VerificationFailed",
                   "VerificationResult",
                   "sha256_file",
                   "sniff_magic",
                   "verify_download",
]
