"""Shared language-model configuration."""

import os

from crewai import LLM


def _llm_settings() -> tuple[str, str]:
    model = os.getenv("GEMINI_MODEL", "").strip()
    if not model:
        raise RuntimeError("GEMINI_MODEL must be set in .env.")

    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY must be set in .env.")

    return model, api_key


def create_llm() -> LLM:
    model, api_key = _llm_settings()
    return LLM(model=model, api_key=api_key)


def validate_llm_configuration() -> None:
    _llm_settings()
