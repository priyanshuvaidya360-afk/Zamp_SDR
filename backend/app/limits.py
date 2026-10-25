"""Per-visitor hourly limits, so a shared demo link can't drain the free API quotas.

Spec: docs/PROJECT_SPEC.md "Deploy checklist". In memory, per client IP, over a sliding hour:
uploads (settings.uploads_per_hour) and prospects sent through the pipeline
(settings.prospects_per_hour; each one spends search credits and model tokens). 0 turns a limit
off. Behind a proxy (Render) the client IP is the first X-Forwarded-For address.
"""

import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

WINDOW_S = 3600
_used: dict[tuple[str, str], deque[tuple[float, int]]] = defaultdict(deque)


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[0].strip() or (request.client.host if request.client else "unknown")


def spend(request: Request, bucket: str, amount: int, limit: int, what: str) -> None:
    """Record `amount` against this visitor's hourly `limit`, or refuse with a readable 429."""
    if limit <= 0 or amount <= 0:
        return
    now = time.monotonic()
    used = _used[(bucket, client_ip(request))]
    while used and now - used[0][0] > WINDOW_S:
        used.popleft()
    total = sum(n for _, n in used)
    if total + amount > limit:
        wait = int(WINDOW_S - (now - used[0][0])) // 60 + 1 if used else 60
        left = max(0, limit - total)
        raise HTTPException(429, f"Hourly limit reached: {limit} {what} per hour on this server "
                                 f"({left} left). Try again in about {wait} minutes.")
    used.append((now, amount))


def reset() -> None:
    """Tests start from a clean slate."""
    _used.clear()
