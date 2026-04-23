"""Message queue — prioritizes mentions over auto-response messages."""

import asyncio
import heapq
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass(order=True)
class Message:
    priority: int = field(init=False, repr=False)
    sort_timestamp: float = field(init=False, repr=False)
    guild_id: int = field(compare=False)
    channel_id: int = field(compare=False)
    content: str = field(compare=False)
    author: str = field(compare=False)
    author_id: int = field(compare=False)
    is_mention: bool = field(default=False, compare=False)
    timestamp: datetime = field(default_factory=datetime.now, compare=False)

    def __post_init__(self):
        self.priority = 0 if self.is_mention else 1
        self.sort_timestamp = self.timestamp.timestamp()


class MessageQueue:
    """Async priority queue for Discord messages.

    Priority 0 = mention (urgent), Priority 1 = auto-response.
    Bounded: when full, the lowest-priority oldest message is dropped on enqueue.
    """

    def __init__(self, max_size: int = 50):
        self._heap = []
        self._max_size = max_size
        self._not_empty = asyncio.Event()

    async def enqueue(self, msg: Message) -> None:
        if len(self._heap) >= self._max_size:
            drop_idx = max(range(len(self._heap)),
                         key=lambda i: (self._heap[i].priority, -self._heap[i].sort_timestamp))
            dropped = self._heap.pop(drop_idx)
            # Re-heapify after arbitrary removal
            heapq.heapify(self._heap)
            logger.warning("Queue full, dropped message from %s in channel %d", dropped.author, dropped.channel_id)
        heapq.heappush(self._heap, msg)
        self._not_empty.set()
        logger.debug("Message enqueued from %s in channel %d (queue size: %d)", msg.author, msg.channel_id, len(self._heap))

    async def dequeue(self, deadline: Optional[float] = None) -> Message:
        loop = asyncio.get_event_loop()
        while not self._heap:
            try:
                await asyncio.wait_for(self._not_empty.wait(), timeout=max(0.01, deadline - loop.time()))
                self._not_empty.clear()
            except asyncio.TimeoutError:
                raise
        return heapq.heappop(self._heap)

    def size(self) -> int:
        return len(self._heap)

    def clear(self) -> None:
        self._heap.clear()
        self._not_empty.clear()
