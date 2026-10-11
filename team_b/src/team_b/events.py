"""Live delivery of messages that arrive for a customer while they are looking at the chat (a human's reply).

An in-process hub: every open chat page subscribes to its conversation and gets an asyncio queue; publishing puts the
message on every queue of that conversation. Messages are also kept in the session outbox, so a page that connects
late (or a restart of the connection) can still read them with GET .../outbox. One process only: with several workers
a shared broker would be needed.
"""

import asyncio
from collections import defaultdict
from typing import Any

MAX_QUEUED = 100  # a slow reader loses its oldest messages, never blocks the sender


class EventHub:
    def __init__(self) -> None:
        self._queues: defaultdict[tuple[str, str], set[asyncio.Queue[dict[str, Any]]]] = defaultdict(set)

    def subscribe(self, tenant_id: str, conversation_id: str) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=MAX_QUEUED)
        self._queues[(tenant_id, conversation_id)].add(queue)
        return queue

    def unsubscribe(self, tenant_id: str, conversation_id: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        listeners = self._queues.get((tenant_id, conversation_id))
        if listeners is not None:
            listeners.discard(queue)
            if not listeners:
                del self._queues[(tenant_id, conversation_id)]

    def subscribers(self, tenant_id: str, conversation_id: str) -> int:
        return len(self._queues.get((tenant_id, conversation_id), ()))

    def publish(self, tenant_id: str, conversation_id: str, event: dict[str, Any]) -> int:
        """Hand the event to every open page of the conversation. Returns how many received it."""
        delivered = 0
        for queue in list(self._queues.get((tenant_id, conversation_id), ())):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(event)
            delivered += 1
        return delivered
