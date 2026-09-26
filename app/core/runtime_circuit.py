from __future__ import annotations

import os
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any

from dotenv import load_dotenv

load_dotenv()


# ============================================================
# CONFIG
# ============================================================

FAILURE_THRESHOLD = int(
    os.getenv("SYNAPSEOPS_CIRCUIT_FAILURE_THRESHOLD", "3")
)


# ============================================================
# CIRCUIT STATE
# ============================================================

@dataclass
class CircuitState:
    failure_count: int = 0
    success_count: int = 0

    state: str = "closed"
    # closed   -> calls allowed
    # open     -> calls blocked

    last_error: str | None = None
    opened_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "failure_count": self.failure_count,
            "success_count": self.success_count,
            "state": self.state,
            "last_error": self.last_error,
            "opened_reason": self.opened_reason,
        }


# ============================================================
# RUNTIME CIRCUIT MANAGER
# ============================================================

@dataclass
class RuntimeCircuitBreaker:
    circuits: dict[str, CircuitState] = field(
        default_factory=dict
    )

    def _get(
        self,
        component: str,
    ) -> CircuitState:

        component = component.strip().lower()

        if component not in self.circuits:
            self.circuits[component] = CircuitState()

        return self.circuits[component]

    # --------------------------------------------------------
    # CALL PERMISSION
    # --------------------------------------------------------

    def allow_call(
        self,
        component: str,
    ) -> bool:

        circuit = self._get(component)

        return circuit.state != "open"

    # --------------------------------------------------------
    # SUCCESS
    # --------------------------------------------------------

    def record_success(
        self,
        component: str,
    ) -> None:

        circuit = self._get(component)

        circuit.success_count += 1

        # Consecutive-failure semantics:
        # a successful call resets the failure streak.
        circuit.failure_count = 0
        circuit.last_error = None

    # --------------------------------------------------------
    # FAILURE
    # --------------------------------------------------------

    def record_failure(
        self,
        component: str,
        error: str,
    ) -> bool:
        """
        Returns True if this failure opened the circuit.
        """

        circuit = self._get(component)

        if circuit.state == "open":
            return True

        circuit.failure_count += 1
        circuit.last_error = str(error)

        if circuit.failure_count >= FAILURE_THRESHOLD:

            circuit.state = "open"

            circuit.opened_reason = (
                f"{component} failed "
                f"{circuit.failure_count} consecutive times. "
                "Further calls are blocked for this workflow."
            )

            return True

        return False

    # --------------------------------------------------------
    # STATE
    # --------------------------------------------------------

    def is_open(
        self,
        component: str,
    ) -> bool:

        return self._get(component).state == "open"

    def reason(
        self,
        component: str,
    ) -> str | None:

        return self._get(component).opened_reason

    # --------------------------------------------------------
    # SERIALIZATION
    # --------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:

        return {
            "failure_threshold": FAILURE_THRESHOLD,
            "circuits": {
                name: circuit.to_dict()
                for name, circuit in self.circuits.items()
            },
        }

    @classmethod
    def from_dict(
        cls,
        raw: dict[str, Any] | None,
    ) -> "RuntimeCircuitBreaker":

        manager = cls()

        if not raw:
            return manager

        circuits = raw.get("circuits", {})

        if not isinstance(circuits, dict):
            return manager

        for name, value in circuits.items():

            if not isinstance(value, dict):
                continue

            manager.circuits[name] = CircuitState(
                failure_count=int(
                    value.get("failure_count", 0)
                ),
                success_count=int(
                    value.get("success_count", 0)
                ),
                state=str(
                    value.get("state", "closed")
                ),
                last_error=value.get("last_error"),
                opened_reason=value.get("opened_reason"),
            )

        return manager


# ============================================================
# CURRENT WORKFLOW CIRCUIT MANAGER
# ============================================================

_CURRENT_CIRCUIT: ContextVar[
    RuntimeCircuitBreaker | None
] = ContextVar(
    "synapseops_runtime_circuit",
    default=None,
)


def activate_circuit(
    circuit: RuntimeCircuitBreaker,
) -> Token:

    return _CURRENT_CIRCUIT.set(circuit)


def deactivate_circuit(
    token: Token,
) -> None:

    _CURRENT_CIRCUIT.reset(token)


def current_circuit() -> RuntimeCircuitBreaker | None:

    return _CURRENT_CIRCUIT.get()