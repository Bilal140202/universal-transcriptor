"""TerminologyMemory — consistent terminology across the entire video (spec §11.2).

"YG" must stay "YG" — never "Y.G." or "Why Gee". Terms are learned from
metadata, prior segments, source-language repetition and a custom glossary,
and can be overridden by the user. Enforcement produces findings, never
silent rewrites.
"""
from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable

from ..models import Finding, TerminologyEntry

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9&'-]{1,}")
# Variants that suggest inconsistency: dots between letters, spacing, casing.
def _variants(term: str) -> set[str]:
    core = term.strip().lower()
    letters = re.sub(r"[^a-z0-9]", "", core)
    out = {core, letters, ".".join(letters), " ".join(letters), "-".join(letters)}
    return {v for v in out if v}


def _variant_pattern(variant: str) -> re.Pattern:
    """Boundary-safe pattern: \b fails after punctuation variants ("Y.G. "),
    so use explicit non-alphanumeric lookarounds instead. Dotted variants
    ("Y.G") may also absorb one trailing dot ("Y.G.") so it isn't left behind.
    """
    body = re.escape(variant)
    tail = r"\.?" if "." in variant else ""
    return re.compile(r"(?<![A-Za-z0-9])" + body + tail + r"(?![A-Za-z0-9])", re.IGNORECASE)


class TerminologyMemory:
    """Term store + enforcement for translation consistency."""

    def __init__(self, entries: list[TerminologyEntry] | None = None) -> None:
        self.entries: list[TerminologyEntry] = entries or []
        self._by_source: dict[str, TerminologyEntry] = {}
        for e in self.entries:
            self._by_source[e.source_term.lower()] = e

    # -- registration ----------------------------------------------------------

    def register(self, source_term: str, english_term: str,
                 confidence: float = 1.0, context: str | None = None,
                 user_override: bool = False) -> TerminologyEntry:
        entry = TerminologyEntry(
            source_term=source_term, english_term=english_term,
            confidence=confidence, context=context, user_override=user_override,
        )
        self.entries.append(entry)
        self._by_source[source_term.lower()] = entry
        return entry

    def override(self, source_term: str, english_term: str) -> TerminologyEntry:
        """User correction — highest precedence, never auto-overwritten."""
        entry = self.register(source_term, english_term, confidence=1.0,
                              user_override=True)
        entry.occurrences = self._by_source.get(source_term.lower(), entry).occurrences
        return entry

    # -- learning ---------------------------------------------------------------

    def learn_from_repetition(self, texts: Iterable[str], min_occurrences: int = 3,
                              min_len: int = 2) -> list[TerminologyEntry]:
        """Learn candidate terms from frequent capitalized tokens across segments.

        This catches brand/group names ("YG", "BABYMONSTER") that recur in the
        source language and must survive into English unchanged.
        """
        counter: Counter[str] = Counter()
        for text in texts:
            for word in _WORD_RE.findall(text or ""):
                if len(word) >= min_len:
                    counter[word] += 1
        learned: list[TerminologyEntry] = []
        for term, count in counter.most_common():
            if count < min_occurrences:
                break
            if term not in self._by_source:
                # Default English rendering: the term itself, unchanged.
                entry = self.register(term, term, confidence=0.6,
                                      context="auto-learned from repetition")
                entry.occurrences = count
                learned.append(entry)
        return learned

    # -- lookup & enforcement -----------------------------------------------------

    def entries_for(self, text: str) -> list[TerminologyEntry]:
        """Entries whose source term literally appears in `text`."""
        hits: list[TerminologyEntry] = []
        low = (text or "").lower()
        for term, entry in self._by_source.items():
            if term and term.lower() in low:
                hits.append(entry)
        return hits

    def overrides(self) -> dict[str, str]:
        """Source → English map handed to translation providers."""
        return {e.source_term: e.english_term for e in self.entries}

    def apply(self, english_text: str) -> str:
        """Enforce registered variants onto canonical English forms.

        Deterministic, faithful, non-creative: only rewrites tokens that match
        a known term or one of its spelling variants. Does NOT paraphrase.
        """
        text = english_text or ""
        for entry in self.entries:
            variants = _variants(entry.source_term) | _variants(entry.english_term)
            for variant in sorted(variants, key=len, reverse=True):
                text = _variant_pattern(variant).sub(entry.english_term, text)
        return text

    def consistency_findings(self, pairs: Iterable[tuple[str, str]]) -> list[Finding]:
        """Scan (source, english) pairs for inconsistent renderings.

        Flags English variants of a known source term that differ from the
        registered English form (e.g. "Why Gee" where "YG" is registered).
        """
        findings: list[Finding] = []
        variant_map: dict[str, TerminologyEntry] = {}
        for entry in self.entries:
            for v in _variants(entry.source_term) | _variants(entry.english_term):
                variant_map[v] = entry

        for idx, (source, english) in enumerate(pairs):
            for term, entry in self._by_source.items():
                if term.lower() in (source or "").lower():
                    expected = entry.english_term
                    for variant in sorted(_variants(term), key=len, reverse=True):
                        if (_variant_pattern(variant).search(english or "")
                                and variant.lower() != expected.lower()):
                            findings.append(Finding(
                                code="TERMINOLOGY_INCONSISTENT",
                                severity="warn",
                                message=(f"Term {expected!r} rendered as {variant!r} "
                                         f"(pair {idx}); registered form is "
                                         f"'{entry.source_term}' → '{expected}'."),
                                suggestion=f"Replace with '{expected}'.",
                            ))
        return findings
