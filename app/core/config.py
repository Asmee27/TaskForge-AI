from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

from app.core.runtime_budget import (
    BUDGET_USAGE_CALLBACK,
)


load_dotenv()


@dataclass(frozen=True)
class Settings:
    llm_provider: str
    llm_model: str
    llm_timeout_seconds: float
    groq_api_key: str | None
    gemini_api_key: str | None
    tavily_api_key: str | None
    tool_timeout_seconds: float
    log_level: str


def _env(
    name: str,
    default: str | None = None,
) -> str | None:
    return os.getenv(
        name,
        default,
    )


def load_settings() -> Settings:

    provider = (
        _env(
            "LLM_PROVIDER",
            "groq",
        )
        or "groq"
    ).lower()

    if provider == "groq":

        model = (
            _env(
                "GROQ_MODEL",
                "openai/gpt-oss-20b",
            )
            or "openai/gpt-oss-20b"
        )

    elif provider == "gemini":

        model = (
            _env(
                "GEMINI_MODEL",
                "gemini-2.0-flash",
            )
            or "gemini-2.0-flash"
        )

    else:
        raise RuntimeError(
            (
                "LLM_PROVIDER must be "
                "'groq' or 'gemini'."
            )
        )

    return Settings(
        llm_provider=provider,
        llm_model=model,
        llm_timeout_seconds=float(
            _env(
                "LLM_TIMEOUT_SECONDS",
                "45",
            )
            or 45
        ),
        groq_api_key=_env(
            "GROQ_API_KEY"
        ),
        gemini_api_key=_env(
            "GEMINI_API_KEY"
        ),
        tavily_api_key=_env(
            "TAVILY_API_KEY"
        ),
        tool_timeout_seconds=float(
            _env(
                "TOOL_TIMEOUT_SECONDS",
                "10",
            )
            or 10
        ),
        log_level=(
            _env(
                "LOG_LEVEL",
                "INFO",
            )
            or "INFO"
        ).upper(),
    )


SETTINGS = load_settings()


def get_llm():

    callbacks = [
        BUDGET_USAGE_CALLBACK,
    ]

    if SETTINGS.llm_provider == "groq":

        if not SETTINGS.groq_api_key:
            raise RuntimeError(
                "GROQ_API_KEY is missing."
            )

        from langchain_groq import (
            ChatGroq,
        )

        return ChatGroq(
            model=SETTINGS.llm_model,
            api_key=SETTINGS.groq_api_key,
            temperature=0,
            request_timeout=SETTINGS.llm_timeout_seconds,
            callbacks=callbacks,
        )

    if SETTINGS.llm_provider == "gemini":

        if not SETTINGS.gemini_api_key:
            raise RuntimeError(
                "GEMINI_API_KEY is missing."
            )

        from langchain_google_genai import (
            ChatGoogleGenerativeAI,
        )

        return ChatGoogleGenerativeAI(
            model=SETTINGS.llm_model,
            google_api_key=(
                SETTINGS.gemini_api_key
            ),
            temperature=0,
            timeout=SETTINGS.llm_timeout_seconds,
            callbacks=callbacks,
        )

    raise RuntimeError(
        "Unsupported LLM provider."
    )