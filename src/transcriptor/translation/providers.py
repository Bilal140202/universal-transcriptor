"""Concrete translation providers: local NLLB (CTranslate2), local LLM, hybrid."""
from __future__ import annotations

import json
import os
import os
from pathlib import Path

from ..models import Finding
from .base import TranslationProvider, TranslationResult

# Default location of a CTranslate2-converted NLLB (int8). CT2 needs ~650 MB
# resident instead of ~2.5 GB fp32 torch — the difference between a working
# low-memory host and an OOM kill. See docs/RESEARCH.md §5.
_DEFAULT_CT2_DIR = "/home/z/models/nllb-600m-ct2-int8"

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

    def __init__(self, model_id: str = "facebook/nllb-200-distilled-600M",
                 ct2_dir: str | None = None) -> None:
        self.model_id = model_id
        env_dir = os.environ.get("TRANSCRIPTOR_NLLB_CT2_DIR")
        self.ct2_dir = ct2_dir or env_dir or (
            _DEFAULT_CT2_DIR if Path(_DEFAULT_CT2_DIR).is_dir() else None)
        self._pipe = None

    def available(self) -> bool:
        try:
            import transformers  # noqa: F401
            return True
        except ImportError:
            return False

    def _ensure(self):
        if self._pipe is None:
            if self.ct2_dir and Path(self.ct2_dir).is_dir():
                self._pipe = self._ensure_ct2()
            else:
                self._pipe = self._ensure_torch()
        return self._pipe

    def _ensure_ct2(self):
        import ctranslate2
        import sentencepiece as spm
        translator = ctranslate2.Translator(self.ct2_dir, compute_type="int8")
        sp = spm.SentencePieceProcessor(
            model_file=str(Path(self.ct2_dir) / "sentencepiece.bpe.model"))
        return ("ct2", translator, sp)

    def _ensure_torch(self):
        import torch
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
        tok = AutoTokenizer.from_pretrained(self.model_id)
        dtype_name = os.environ.get("TRANSCRIPTOR_MT_DTYPE", "auto").lower()
        requested = {"float32": torch.float32, "bfloat16": torch.bfloat16,
                     "float16": torch.float16}.get(dtype_name)
        # low_cpu_mem_usage: stream weights into the target dtype instead of
        # materializing a full fp32 copy first — halves load-time peak RAM.
        common = dict(low_cpu_mem_usage=True)
        if requested is None:  # auto: fp32 preferred, fall back on pressure
            try:
                model = AutoModelForSeq2SeqLM.from_pretrained(self.model_id, **common)
            except (MemoryError, RuntimeError) as exc:
                print(f"[nllb] fp32 load failed ({exc}); retrying bfloat16",
                      flush=True)
                model = AutoModelForSeq2SeqLM.from_pretrained(
                    self.model_id, dtype=torch.bfloat16, **common)
        else:
            model = AutoModelForSeq2SeqLM.from_pretrained(
                self.model_id, dtype=requested, **common)
        model.eval()
        return ("torch", tok, model)

    def unload(self) -> None:
        if self._pipe is not None:
            self._pipe = None
            import gc
            gc.collect()

    def translate_batch(self, windows, source_language: str) -> list[TranslationResult]:
        pipe = self._ensure()
        if pipe[0] == "ct2":
            return self._translate_ct2(pipe, windows, source_language)
        return self._translate_torch(pipe, windows, source_language)

    def _translate_ct2(self, pipe, windows, source_language: str) -> list[TranslationResult]:
        """CTranslate2 int8 batch translation — ~650 MB resident, CPU-fast."""
        import re
        _, translator, sp = pipe
        lang_tag = re.compile(r"^[a-z]{3}_[A-Z][a-zA-Z]{3,4}$")
        src = _NLLB_CODES.get(source_language or "", "eng_Latn")
        if not windows:
            return []
        batch = [(w, [src] + sp.encode(w.segment.text, out_type=str))
                 for w in windows]
        try:
            outputs = translator.translate_batch(
                [tokens for _, tokens in batch],
                target_prefix=[["eng_Latn"]] * len(batch),
                max_batch_size=16,
            )
        except Exception as exc:
            return [TranslationResult(
                w.segment_id, provider=self.name, ok=False,
                findings=[Finding(code="MT_FAILED", severity="error",
                                  message=f"NLLB(CT2) failed: {exc}")],
            ) for w, _ in batch]
        results: list[TranslationResult] = []
        for (w, _), out in zip(batch, outputs):
            try:
                best = out.hypotheses[0] if out.hypotheses else []
                toks = [t for t in best if not lang_tag.match(t)]
                english = sp.decode(toks).strip()
                results.append(TranslationResult(w.segment_id, english, self.name))
            except Exception as exc:
                results.append(TranslationResult(
                    w.segment_id, provider=self.name, ok=False,
                    findings=[Finding(code="MT_FAILED", severity="error",
                                      message=f"NLLB(CT2) decode failed on "
                                      f"{w.segment_id}: {exc}")],
                ))
        return results

    def _translate_torch(self, pipe, windows, source_language: str) -> list[TranslationResult]:
        tok, model = pipe[1], pipe[2]
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

    def __init__(self, model: str | None = None,
                 base_url: str | None = None, api_key: str | None = None) -> None:
        # Environment overrides let deployments (web bridge, containers) wire
        # any OpenAI-compatible endpoint — local llama.cpp/Ollama/vLLM or the
        # sandbox SDK proxy — without code changes.
        self.model = (model or os.environ.get("TRANSCRIPTOR_LLM_MODEL")
                      or "qwen2.5-7b-instruct")
        self.base_url = (base_url if base_url is not None
                         else os.environ.get("TRANSCRIPTOR_LLM_BASE_URL"))
        self.api_key = (api_key if api_key is not None
                        else os.environ.get("TRANSCRIPTOR_LLM_API_KEY", "local"))

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
                    messages=[
                        {"role": "system",
                         "content": SYSTEM_PROMPT.format(source=source_language or "the source")},
                        {"role": "user", "content": user_msg},
                    ],
                    temperature=0.2,
                )
                data = _parse_llm_json(resp.choices[0].message.content)
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


def _parse_llm_json(content: str) -> dict:
    """Parse the translator's STRICT-JSON reply defensively.

    Tolerates ```json fences and stray prose around the object — the model is
    instructed to reply with JSON only, but enforcing structure beats failing
    a whole batch over a markdown fence.
    """
    text = (content or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start:end + 1]
    return json.loads(text)


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
