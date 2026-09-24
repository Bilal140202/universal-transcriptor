"""ASRRouter — selects provider/model/compute per requirements (spec §8.2).

Inputs: language, audio quality, duration, GPU availability, RAM/VRAM, speaker
count, accuracy expectation, word-timestamp requirement.
Output: a routing decision (provider instance + config) — never a hard-coded model.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .base import ASRProvider


def gpu_available() -> bool:
    """Detect CUDA without importing torch at module import time."""
    try:
        import ctranslate2  # faster-whisper's runtime
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        pass
    try:
        import torch
        return torch.cuda.is_available()
    except Exception:
        return False


@dataclass
class RouteDecision:
    provider_name: str
    model: str
    compute_type: str
    batch_size: int = 16
    vad_strategy: str = "silero"
    alignment_strategy: str = "native"        # native | whisperx | forced
    reason: str = ""
    extras: dict[str, Any] = field(default_factory=dict)


class ASRRouter:
    """Policy table + environment detection. Providers register by name."""

    def __init__(self, providers: dict[str, ASRProvider] | None = None) -> None:
        self.providers: dict[str, ASRProvider] = providers or {}

    def register(self, provider: ASRProvider) -> None:
        self.providers[provider.name] = provider

    # requirements: accuracy=best|balanced|fast, gpu=auto|bool, max_vram_gb, language
    def route(self, requirements: dict[str, Any] | None = None) -> tuple[ASRProvider, RouteDecision]:
        req = requirements or {}
        gpu = req.get("gpu", "auto")
        if gpu == "auto":
            gpu = gpu_available()
        accuracy = req.get("accuracy", "balanced")
        language = req.get("language")

        if accuracy == "best" and gpu:
            decision = RouteDecision(
                provider_name="faster-whisper", model="large-v3",
                compute_type="float16",
                alignment_strategy="whisperx" if req.get("word_timestamps", True) else "native",
                reason="GPU present + accuracy=best → high-accuracy multilingual model.",
            )
        elif accuracy == "best":
            decision = RouteDecision(
                provider_name="faster-whisper", model="large-v3",
                compute_type="int8", batch_size=8,
                reason="CPU-only + accuracy=best → int8 large model (slower).",
            )
        elif accuracy == "fast":
            decision = RouteDecision(
                provider_name="faster-whisper", model="small",
                compute_type="int8",
                alignment_strategy="native",
                reason="accuracy=fast → efficient model for long/low-resource runs.",
            )
        else:  # balanced
            decision = RouteDecision(
                provider_name="faster-whisper", model="distil-large-v3" if gpu else "base",
                compute_type="float16" if gpu else "int8",
                alignment_strategy="whisperx" if gpu and req.get("word_timestamps", True) else "native",
                reason="accuracy=balanced → distilled model on GPU, base on CPU.",
            )

        # Language-optimized overrides live here, provider-driven (spec: never
        # hard-code one model; overrides may be extended per benchmark results).
        if language in ("zh", "yue"):
            decision.extras["language_note"] = (
                "Mandarin/Cantonese: consider SenseVoice/Paraformer adapters for "
                "higher CER; benchmark before production."
            )

        provider = self.providers.get(decision.provider_name)
        if provider is None:
            raise RuntimeError(
                f"Routed to {decision.provider_name!r} but no such provider is "
                f"registered. Available: {sorted(self.providers)}."
            )
        if not provider.available():
            raise RuntimeError(
                f"Provider {decision.provider_name!r} is not available in this "
                "environment. Install the matching extra, e.g. "
                "`pip install -e \".[asr]\"` for faster-whisper."
            )
        self._configure(provider, decision)
        return provider, decision

    @staticmethod
    def _configure(provider: ASRProvider, decision: RouteDecision) -> None:
        """Push the routing decision onto the provider instance.

        Providers are registered singletons with default model configs; the
        router's decision (model size, compute type, device) is authoritative.
        Without this, a decision of 'base/int8' would silently transcribe with
        the provider's default 'large-v3'.
        """
        if hasattr(provider, "model_size"):
            provider.model_size = decision.model
        if hasattr(provider, "compute_type"):
            provider.compute_type = decision.compute_type
        if hasattr(provider, "batch_size"):
            provider.batch_size = decision.batch_size
