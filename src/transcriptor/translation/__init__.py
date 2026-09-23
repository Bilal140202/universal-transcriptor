"""Translation subpackage — context-aware providers, windows, terminology."""
from .base import TranslationProvider, TranslationResult
from .context import ContextWindow, build_context_windows
from .terminology import TerminologyMemory

__all__ = [
           "ContextWindow",
           "TerminologyMemory",
           "TranslationProvider",
           "TranslationResult",
           "build_context_windows",
]
