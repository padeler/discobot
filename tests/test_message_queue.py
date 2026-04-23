"""Tests for engine/message_queue.py — Message and MessageQueue."""

import asyncio
from datetime import datetime

import pytest
from engine.message_queue import Message, MessageQueue


class TestMessage:
    def test_creation(self):
        msg = Message(
            guild_id=111,
            channel_id=222,
            content="hello bot",
            author="alice",
            author_id=333,
            is_mention=True,
        )
        assert msg.guild_id == 111
        assert msg.channel_id == 222
        assert msg.content == "hello bot"
        assert msg.author == "alice"
        assert msg.author_id == 333
        assert msg.is_mention is True

    def test_mention_priority_is_zero(self):
        msg = Message(1, 2, "hi", "u", 3, is_mention=True)
        assert msg.priority == 0

    def test_auto_response_priority_is_one(self):
        msg = Message(1, 2, "hi", "u", 3, is_mention=False)
        assert msg.priority == 1

    def test_timestamp_default(self):
        msg = Message(1, 2, "hi", "u", 3)
        assert isinstance(msg.timestamp, datetime)


class TestMessageQueue:
    @pytest.mark.asyncio
    async def test_enqueue_and_dequeue(self):
        q = MessageQueue()
        msg = Message(1, 2, "hello", "alice", 100, is_mention=True)
        await q.enqueue(msg)
        dequeued = await q.dequeue(asyncio.get_event_loop().time() + 0.1)
        assert dequeued is msg
        assert dequeued.content == "hello"

    @pytest.mark.asyncio
    async def test_mention_dequeued_before_auto(self):
        q = MessageQueue()
        auto_msg = Message(1, 2, "auto", "bob", 200, is_mention=False)
        mention_msg = Message(1, 2, "mention", "alice", 100, is_mention=True)

        await q.enqueue(auto_msg)
        await q.enqueue(mention_msg)

        first = await q.dequeue(asyncio.get_event_loop().time() + 0.1)
        assert first.is_mention is True
        assert first.content == "mention"

    @pytest.mark.asyncio
    async def test_size(self):
        q = MessageQueue()
        assert q.size() == 0
        await q.enqueue(Message(1, 2, "a", "u", 1))
        assert q.size() == 1
        await q.enqueue(Message(1, 2, "b", "u", 1))
        assert q.size() == 2

    @pytest.mark.asyncio
    async def test_dequeue_timeout_when_empty(self):
        q = MessageQueue()
        with pytest.raises(asyncio.TimeoutError):
            await q.dequeue(asyncio.get_event_loop().time() + 0.05)

    @pytest.mark.asyncio
    async def test_clear(self):
        q = MessageQueue()
        await q.enqueue(Message(1, 2, "a", "u", 1))
        await q.enqueue(Message(1, 2, "b", "u", 1))
        q.clear()
        assert q.size() == 0

    @pytest.mark.asyncio
    async def test_max_queue_size(self):
        q = MessageQueue(max_size=2)
        await q.enqueue(Message(1, 2, "a", "u", 1, is_mention=True))
        await q.enqueue(Message(1, 2, "b", "u", 1, is_mention=False))
        # Third enqueue should drop the auto-response (priority 1), not the mention
        await q.enqueue(Message(1, 2, "c", "u", 1, is_mention=False))
        assert q.size() == 2
        # The mention should survive
        first = await q.dequeue(asyncio.get_event_loop().time() + 0.1)
        assert first.is_mention is True

    @pytest.mark.asyncio
    async def test_max_queue_size_all_same_priority(self):
        import time
        q = MessageQueue(max_size=2)
        await q.enqueue(Message(1, 2, "a", "u", 1, is_mention=False))
        time.sleep(0.01)
        await q.enqueue(Message(1, 2, "b", "u", 1, is_mention=False))
        time.sleep(0.01)
        # Third enqueue should drop the oldest
        await q.enqueue(Message(1, 2, "c", "u", 1, is_mention=False))
        assert q.size() == 2
