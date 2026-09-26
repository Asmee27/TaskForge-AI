from __future__ import annotations
import re
import time
from typing import Any
from app.core.logging import log_event
from app.core.runtime_circuit import current_circuit

MAX_LLM_ATTEMPTS = 3


def _is_rate_limit(exc: Exception) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    return "429" in text or "rate limit" in text or "ratelimit" in text


def _retry_delay(exc: Exception, attempt: int) -> float:
    text = str(exc)
    patterns = [
        r"retry(?:ing)?\s+(?:in|after)\s+([0-9.]+)\s*s",
        r"try again in\s+([0-9.]+)\s*s",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if m:
            try:
                return min(float(m.group(1)), 5.0)
            except ValueError:
                pass
    return min(0.8 * (2 ** attempt), 5.0)


def invoke_llm_with_retry(llm: Any, messages: Any, *, component: str):
    """
    Invoke an LLM with bounded rate-limit retries and a workflow-scoped
    provider circuit breaker.

    Circuit semantics:
    - A successful provider call resets the consecutive LLM failure streak.
    - A failed provider attempt is recorded once after this helper exhausts
      its internal retry policy (not once per retry).
    - Once the workflow-level ``llm`` circuit opens, later agent calls are
      blocked before reaching the provider.
    """
    runtime_circuit = current_circuit()

    if (
        runtime_circuit is not None
        and not runtime_circuit.allow_call("llm")
    ):
        reason = (
            runtime_circuit.reason("llm")
            or "LLM provider circuit is open for this workflow."
        )

        log_event(
            "CIRCUIT_BREAKER",
            (
                "LLM call blocked because circuit is OPEN "
                f"| component={component} "
                f"| reason={reason}"
            ),
        )

        raise RuntimeError(
            f"LLM_CIRCUIT_OPEN: {reason}"
        )

    last_exc: Exception | None = None

    for attempt in range(MAX_LLM_ATTEMPTS):
        started = time.perf_counter()
        log_event(
            "LLM",
            (
                f"{component} invocation started "
                f"| attempt={attempt + 1}/{MAX_LLM_ATTEMPTS}"
            ),
        )

        try:
            response = llm.invoke(messages)

            log_event(
                "LLM",
                (
                    f"{component} invocation completed "
                    f"| latency_ms={int((time.perf_counter() - started) * 1000)}"
                ),
            )

            if runtime_circuit is not None:
                runtime_circuit.record_success("llm")

            return response

        except Exception as exc:
            last_exc = exc

            log_event(
                "LLM",
                (
                    f"{component} invocation failed "
                    f"| latency_ms={int((time.perf_counter() - started) * 1000)} "
                    f"| error={type(exc).__name__}: {exc}"
                ),
            )

            should_retry = (
                _is_rate_limit(exc)
                and attempt < MAX_LLM_ATTEMPTS - 1
            )

            if should_retry:
                delay = _retry_delay(
                    exc,
                    attempt,
                )

                log_event(
                    "RETRY",
                    (
                        f"{component} rate limited "
                        f"| retry {attempt + 1}/"
                        f"{MAX_LLM_ATTEMPTS - 1} "
                        f"| sleeping {delay:.2f}s"
                    ),
                )

                time.sleep(delay)
                continue

            # Count one failed logical LLM operation only after the
            # helper has exhausted its own bounded retry policy.
            if runtime_circuit is not None:
                opened = runtime_circuit.record_failure(
                    "llm",
                    (
                        f"{component}: "
                        f"{type(exc).__name__}: {exc}"
                    ),
                )

                if opened:
                    log_event(
                        "CIRCUIT_BREAKER",
                        (
                            "LLM circuit OPENED "
                            f"| component={component} "
                            f"| reason="
                            f"{runtime_circuit.reason('llm')}"
                        ),
                    )

            raise

    assert last_exc is not None
    raise last_exc
