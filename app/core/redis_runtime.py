from __future__ import annotations

import json
import os
import socket
import uuid
from typing import Any

import redis
from dotenv import load_dotenv
from redis.backoff import ExponentialBackoff
from redis.retry import Retry


load_dotenv()

REDIS_URL = os.getenv(
    "SYNAPSEOPS_REDIS_URL",
    "redis://127.0.0.1:6379/0",
)

TRACE_TTL_SECONDS = int(
    os.getenv("SYNAPSEOPS_TRACE_TTL_SECONDS", "86400")
)

RUN_LEASE_TTL_SECONDS = int(
    os.getenv("SYNAPSEOPS_RUN_LEASE_TTL_SECONDS", "900")
)


def client() -> redis.Redis:
    return redis.Redis.from_url(
        REDIS_URL,
        decode_responses=True,
        socket_connect_timeout=3,
        socket_timeout=3,
        health_check_interval=30,
        retry=Retry(ExponentialBackoff(), 3),
        retry_on_timeout=True,
    )


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

def redis_health() -> dict[str, Any]:
    try:
        ok = bool(client().ping())

        return {
            "ok": ok,
            "backend": "redis",
        }

    except Exception as exc:
        return {
            "ok": False,
            "backend": "redis",
            "error": f"{type(exc).__name__}: {exc}",
        }


# ---------------------------------------------------------------------------
# Trace cache
# ---------------------------------------------------------------------------

def cache_trace_event(
    run_id: str,
    event: dict[str, Any],
) -> None:
    """
    Cache workflow trace events in Redis.

    PostgreSQL remains the durable source of truth.
    Redis is only the fast trace cache.
    """

    try:
        r = client()

        key = f"synapseops:trace:{run_id}"

        r.rpush(
            key,
            json.dumps(event, default=str),
        )

        r.expire(
            key,
            TRACE_TTL_SECONDS,
        )

    except Exception:
        # Redis failure must never destroy workflow progress.
        pass


def get_cached_trace_events(
    run_id: str,
) -> list[dict[str, Any]]:
    try:
        values = client().lrange(
            f"synapseops:trace:{run_id}",
            0,
            -1,
        )

        return [
            json.loads(item)
            for item in values
        ]

    except Exception:
        return []


# ---------------------------------------------------------------------------
# Workflow run lease
# ---------------------------------------------------------------------------

def _lease_key(run_id: str) -> str:
    return f"synapseops:lease:{run_id}"


def create_lease_owner() -> str:
    """
    Generate an identity for one workflow execution attempt.

    This prevents one process from accidentally releasing another
    process's lease.
    """

    return (
        f"{socket.gethostname()}:"
        f"{os.getpid()}:"
        f"{uuid.uuid4().hex}"
    )


def acquire_run_lease(
    run_id: str,
    owner: str | None = None,
    ttl_seconds: int | None = None,
) -> bool:
    """
    Acquire a workflow execution lease.

    NX guarantees that two live workers cannot execute the same
    workflow simultaneously.

    The TTL guarantees automatic recovery if the owning process dies.
    """

    try:
        ttl = ttl_seconds or RUN_LEASE_TTL_SECONDS
        value = owner or create_lease_owner()

        return bool(
            client().set(
                _lease_key(run_id),
                value,
                nx=True,
                ex=ttl,
            )
        )

    except Exception:
        # PostgreSQL remains the durable source of truth.
        #
        # We intentionally fail open here so temporary Redis
        # unavailability does not make durable workflows unavailable.
        return True


def get_run_lease(
    run_id: str,
) -> dict[str, Any] | None:
    """
    Inspect the current lease without modifying it.
    """

    try:
        r = client()

        key = _lease_key(run_id)

        owner = r.get(key)

        if owner is None:
            return None

        return {
            "run_id": run_id,
            "owner": owner,
            "ttl_seconds": r.ttl(key),
        }

    except Exception:
        return None


def refresh_run_lease(
    run_id: str,
    owner: str,
    ttl_seconds: int | None = None,
) -> bool:
    """
    Refresh a lease only when it still belongs to this execution owner.
    """

    try:
        ttl = ttl_seconds or RUN_LEASE_TTL_SECONDS

        script = """
        if redis.call("GET", KEYS[1]) == ARGV[1] then
            return redis.call("EXPIRE", KEYS[1], ARGV[2])
        else
            return 0
        end
        """

        result = client().eval(
            script,
            1,
            _lease_key(run_id),
            owner,
            ttl,
        )

        return bool(result)

    except Exception:
        return False


def release_run_lease(
    run_id: str,
    owner: str | None = None,
) -> None:
    """
    Release a workflow lease.

    When an owner is supplied, Redis deletes the key only if the
    caller still owns it. This prevents one worker from releasing
    another worker's active lease.

    The owner-less mode is retained for compatibility with the
    existing workflow runner.
    """

    try:
        r = client()

        if owner is None:
            r.delete(_lease_key(run_id))
            return

        script = """
        if redis.call("GET", KEYS[1]) == ARGV[1] then
            return redis.call("DEL", KEYS[1])
        else
            return 0
        end
        """

        r.eval(
            script,
            1,
            _lease_key(run_id),
            owner,
        )

    except Exception:
        pass


def force_release_run_lease(
    run_id: str,
) -> bool:
    """
    Administrative/recovery operation.

    This must NOT be used for normal duplicate-run handling.
    It exists for a recovery path that has already verified through
    PostgreSQL that the previous execution is no longer active.
    """

    try:
        return bool(
            client().delete(
                _lease_key(run_id)
            )
        )

    except Exception:
        return False