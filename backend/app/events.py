"""In-process pub/sub for live updates, and SSE formatting.

Spec: docs/GUIDE.md T05. Topics are "run:<id>" and "batch:<id>"; each SSE connection holds
one asyncio.Queue. Events are saved to the database before they are published, so a client
that connects late replays them from the database and never misses one.
"""

import asyncio
import json
from collections import defaultdict
from typing import Any

from fastapi.responses import StreamingResponse

HEARTBEAT = ": ping\n\n"


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)

    def subscribe(self, topic: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers[topic].add(queue)
        return queue

    def unsubscribe(self, topic: str, queue: asyncio.Queue) -> None:
        self._subscribers[topic].discard(queue)
        if not self._subscribers[topic]:
            del self._subscribers[topic]

    def publish(self, topic: str, event: dict[str, Any]) -> None:
        for queue in list(self._subscribers.get(topic, ())):
            queue.put_nowait(event)


bus = EventBus()


def format_sse(event: dict[str, Any]) -> str:
    """One SSE message. Stage events carry an id so EventSource can resume (Last-Event-ID)."""
    lines = []
    if event.get("type") == "stage" and event.get("id") is not None:
        lines.append(f"id: {event['id']}")
    lines.append(f"event: {event['type']}")
    lines.append("data: " + json.dumps(event, default=str, ensure_ascii=False))
    return "\n".join(lines) + "\n\n"


def sse_response(generator) -> StreamingResponse:
    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
