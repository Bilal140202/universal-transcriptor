"""Concrete translation providers: local NLLB (CTranslate2), local LLM, hybrid."""
from __future__ import annotations

import json

from ..models import Finding
from .base import TranslationProvider, TranslationResult

# NLLB language code mapping for common Whisper codes.
_NLLB_CODES = {
    "ja": "jpn_Jpan", "ko": "kor_Hang", "zh": "zho_Hans", "yue": "yue_Hant",
    "en": "eng_Latn", "es": "spa_Latn", "fr": "fra_Latn", "de": "deu_Latn",
    "it": "ita_Latn", "pt": "por_Latn", "ru": "rus_Cyrl", "ar": "arb_Arab",
    "hi": "hin_Deva", "ur": "urd_Arab", "bn": "ben_Beng", "th": "tha_Thai",
    "vi": "vie_Latn", "id": "ind_Latn", "tr": "tur_Latn",
}


class NLLBTranslationProvider(TranslationProvider):
    """Local NLLB-200 via CTranslate2/transformers — no cloud, no API keys.

    Sentence-level quality; context windows are respected by batching but the
    model itself is not conversational. Pairs well with an LLM reviewer.
    """

    name = "nllb"
    modes = ("LOCAL_NLLB",)

    def __init__(self, model_id: str = "facebook/nllb-200-distilled-600M") -> None:
        self.model_id = model_id
        self._pipe = None

    def available(self) -> bool:
        try:
            import transformers  # noqa: F401
            return True
        except ImportError:
            return False

    def _ensure(self):
        if self._pipe is None:
            from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
            tok = AutoTokenizer.from_pretrained(self.model_id)
            model = AutoModelForSeq2SeqLM.from_pretrained(self.model_id)
            self._pipe = (tok, model)
        return self._pipe

    def translate_batch(self, windows, source_language: str) -> list[TranslationResult]:
        tok, model = self._ensure()
        src = _NLLB_CODES.get(source_language or "", "eng_Latn")
        results: list[TranslationResult] = []
        for w in windows:
            glossary_note = ""
            if w.glossary:
                keep = ", ".join(f"{k} → {v}" for k, v in list(w.glossary.items())[:12])
                glossary_note = f" Keep these terms unchanged: {keep}."
            prompt = w.segment.text + glossary_note  # NLLB is seq2seq; the note
            # nudges entity preservation but cannot force it — the terminology
            # layer enforces post-hoc via TerminologyMemory.apply().
            try:
                tok.src_lang = src
                inputs = tok(prompt, return_tensors="pt", truncation=True, max_length=512)
                gen = model.generate(**inputs, forced_bos_token_id=tok.convert_tokens_to_ids("eng_Latn"),
                                     max_new_tokens=256)
                english = tok.batch_decode(gen, skip_special_tokens=True)[0]
                results.append(TranslationResult(w.segment_id, english, self.name))
            except Exception as exc:
                results.append(TranslationResult(
                    w.segment_id, provider=self.name, ok=False,
                    findings=[Finding(code="MT_FAILED", severity="error",
                                      message=f"NLLB failed on {w.segment_id}: {exc}")],
                ))
        return results


SYSTEM_PROMPT = """You are a professional subtitle translator. You translate {source} \
speech into natural, faithful English subtitles.

Rules:
- Preserve meaning exactly. Never invent, omit, or "improve" content.
- Keep every glossary term EXACTLY as given (e.g. brand names, group names).
- Keep the register of the speaker; subtitles style: concise, no speaker labels.
- Reply with STRICT JSON only: {{"translations":[{{"segment_id":"...","english":"..."}}]}}
- One entry per input segment, same segment_id, same order. No extra keys."""


class LLMTranslationProvider(TranslationProvider):
    """Local/cloud LLM translator with strict JSON, context windows, glossary.

    Works with any OpenAI-compatible endpoint (local llama.cpp/vLLM/Ollama or
    a cloud API). Cloud is optional and only used when explicitly configured.
    """

    name = "llm"
    modes = ("LOCAL_LLM", "CLOUD_PROVIDER")

    def __init__(self, model: str = "qwen2.5-7b-instruct",
                 base_url: str | None = None, api_key: str | None = None) -> None:
        self.model = model
        self.base_url = base_url          # None → standard OpenAI endpoint
        self.api_key = api_key

    def available(self) -> bool:
        try:
            import openai  # noqa: F401
            return True
        except ImportError:
            return False

    def translate_batch(self, windows, source_language: str) -> list[TranslationResult]:
        from openai import OpenAI
        client = OpenAI(base_url=self.base_url, api_key=self.api_key or "local")
        results: list[TranslationResult] = []
        batch_size = 20
        for start in range(0, len(windows), batch_size):
            batch = windows[start:start + batch_size]
            payload = []
            for w in batch:
                payload.append({
                    "segment_id": w.segment_id,
                    "text": w.segment.text,
                    "speaker": w.speaker,
                    "previous": w.previous.text if w.previous else None,
                    "next": w.next.text if w.next else None,
                })
            glossary = batch[0].glossary if batch else {}
            user_msg = (
                f"Document: {batch[0].document_title or 'untitled'}\n"
                f"Source language: {source_language}\n"
                + (f"Terminology (must stay EXACT): {json.dumps(glossary, ensure_ascii=False)}\n"
                   if glossary else "")
                + "Segments:\n" + json.dumps(payload, ensure_ascii=False)
            )
            try:
                resp = client.chat.completions.create(
                    model=self.model,
                    response_format={"type": "json_object"},
                    messages=[
                        {"role": "system",
                         "content": SYSTEM_PROMPT.format(source=source_language or "the source")},
                        {"role": "user", "content": user_msg},
                    ],
                    temperature=0.2,
                )
                data = json.loads(resp.choices[0].message.content)
                got = {t["segment_id"]: t.get("english", "") for t in data.get("translations", [])}
                for w in batch:
                    if w.segment_id in got:
                        results.append(TranslationResult(w.segment_id, got[w.segment_id], self.name))
                    else:
                        # Contract: unmappable output is a finding, never silence.
                        results.append(TranslationResult(
                            w.segment_id, provider=self.name, ok=False,
                            findings=[Finding(
                                code="MT_UNMAPPED", severity="error",
                                message=f"Translator returned no entry for {w.segment_id}.",
                                segment_id=w.segment_id,
                            )],
                        ))
            except Exception as exc:
                for w in batch:
                    results.append(TranslationResult(
                        w.segment_id, provider=self.name, ok=False,
                        findings=[Finding(code="MT_FAILED", severity="error",
                                          message=f"LLM batch failed: {exc}",
                                          segment_id=w.segment_id)],
                    ))
        return results


class AutoTranslationProvider(TranslationProvider):
    """HYBRID/AUTO: LLM primary when configured, NLLB fallback, per-window."""

    name = "auto"
    modes = ("HYBRID", "AUTO")

    def __init__(self, llm: TranslationProvider | None = None,
                 nllb: TranslationProvider | None = None) -> None:
        self.llm = llm or LLMTranslationProvider()
        self.nllb = nllb or NLLBTranslationProvider()

    def available(self) -> bool:
        return self.llm.available() or self.nllb.available()

    def translate_batch(self, windows, source_language: str) -> list[TranslationResult]:
        primary = self.llm if self.llm.available() else self.nllb
        secondary = self.nllb if primary is self.llm else self.llm
        results = primary.translate_batch(windows, source_language)
        retry_idx = [i for i, r in enumerate(results) if not r.ok]
        if retry_idx and secondary.available():
            retries = secondary.translate_batch(
                [windows[i] for i in retry_idx], source_language)
            for i, sub in zip(retry_idx, retries, strict=True):
                results[i] = sub
        return results
