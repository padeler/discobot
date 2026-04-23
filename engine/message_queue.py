"""Message queue — prioritizes mentions over auto-response messages."""

import asyncio
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
    """Thread-safe async priority queue for Discord messages.

    Priority 0 = mention (urgent), Priority 1 = auto-response.
    Bounded: when full, the lowest-priority message is dropped on enqueue.
    """

    def __init__(self, max_size: int = 50):
        self._queue: asyncio.PriorityQueue[Message] = asyncio.PriorityQueue()
        self._max_size = max_size

    async def enqueue(self, msg: Message) -> None:
        if self._queue.qsize() >= self._max_size:
            # Drop the lowest-priority (highest number) message
            try:
                dropped = self._queue.get_nowait()
                logger.warning("Queue full, dropped message from %s in channel %d", dropped.author, dropped.channel_id)
            except asyncio.QueueEmpty:
                pass
        await self._queue.put(msg)
        logger.debug("Message enqueued from %s in channel %d (queue size: %d)", msg.author, msg.channel_id, self._queue.qsize())

    async def dequeue(self, deadline: Optional[float] = None) -> Message:
        if deadline is not None:
            remaining = max(0.01, deadline - asyncio.get_event_loop().time())
            try:
                return await asyncio.wait_for(self._queue.get(), timeout=remaining)
            except asyncio.TimeoutError:
                raise
        return await self._queue.get()

    def size(self) -> int:
        return self._queue.qsize()

    def clear(self) -> None:
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
