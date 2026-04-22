"""Tests for engine/memory_engine.py - Memory and MemoryEngine."""

import json
from datetime import datetime

import pytest
from engine.memory_engine import Memory, MemoryEngine, _keyword_search, get_tools


class TestMemory:
    def test_to_dict_from_dict_roundtrip(self):
        m = Memory(user="@alice", content="hello world", channel_id=123, author_id=456)
        d = m.to_dict()
        assert d["content"] == "hello world"
        assert d["user"] == "@alice"
        assert d["channel_id"] == 123
        assert d["author_id"] == 456
        assert "id" in d
        assert "created_at" in d

        m2 = Memory.from_dict(d)
        assert m2.content == m.content
        assert m2.user == m.user
        assert m2.channel_id == m.channel_id
        assert m2.id == m.id

    def test_id_is_uuid8(self):
        m = Memory(user="u", content="c", channel_id=1)
        assert len(m.id) == 8

    def test_created_at_is_iso(self):
        m = Memory(user="u", content="c", channel_id=1)
        datetime.fromisoformat(m.created_at)  # should not raise


class TestMemoryEngine:
    @pytest.mark.asyncio
    async def test_store_memory(self, tmp_memories):
        result = await tmp_memories.store(user="@alice", content="test fact", channel_id=123)
        assert result["content"] == "test fact"
        assert "id" in result

    @pytest.mark.asyncio
    async def test_store_persists(self, tmp_memories):
        await tmp_memories.store(user="u", content="persist me", channel_id=1)
        engine2 = MemoryEngine()
        await engine2.load()
        assert len(engine2.memories) == 1
        assert engine2.memories[0].content == "persist me"

    @pytest.mark.asyncio
    async def test_recall_all(self, tmp_memories):
        await tmp_memories.store(user="u1", content="fact one", channel_id=1)
        await tmp_memories.store(user="u2", content="fact two", channel_id=1)
        # Patch the module singleton so execute_tool uses our fixture's engine
        import engine.memory_engine as me_module
        orig_get = me_module.get_engine
        me_module.get_engine = lambda: tmp_memories
        try:
            result = await me_module.execute_tool("recall", {})
            assert "fact one" in result
            assert "fact two" in result
        finally:
            me_module.get_engine = orig_get

    @pytest.mark.asyncio
    async def test_recall_search(self, tmp_memories):
        await tmp_memories.store(user="u1", content="the weather in crete is sunny", channel_id=1)
        await tmp_memories.store(user="u2", content="server restart at midnight", channel_id=1)
        import engine.memory_engine as me_module
        orig_get = me_module.get_engine
        me_module.get_engine = lambda: tmp_memories
        try:
            result = await me_module.execute_tool("recall", {"query": "weather crete"})
            assert "weather" in result or "sunny" in result
            # Both match with default threshold=0.0, but "weather" should appear first
            assert result.index("weather") < result.index("server")
        finally:
            me_module.get_engine = orig_get

    @pytest.mark.asyncio
    async def test_recall_no_match(self, tmp_memories):
        await tmp_memories.store(user="u", content="hello", channel_id=1)
        import engine.memory_engine as me_module
        orig_get = me_module.get_engine
        me_module.get_engine = lambda: tmp_memories
        try:
            result = await me_module.execute_tool("recall", {"query": "xyznonexistent"})
            # With default threshold=0.0, the search returns all items
            assert "Found 1 memory" in result
        finally:
            me_module.get_engine = orig_get

    @pytest.mark.asyncio
    async def test_recall_no_match_threshold(self, tmp_memories):
        await tmp_memories.store(user="u", content="hello", channel_id=1)
        # With threshold=0.01, search should not match unrelated queries
        results = await tmp_memories.search("xyznonexistent", threshold=0.01)
        assert results == []

    @pytest.mark.asyncio
    async def test_list_memories(self, tmp_memories):
        await tmp_memories.store(user="u1", content="m1", channel_id=100)
        await tmp_memories.store(user="u2", content="m2", channel_id=200)
        items = await tmp_memories.list_memories(channel_id=100)
        assert len(items) == 1
        assert items[0]["user"] == "u1"

    @pytest.mark.asyncio
    async def test_list_memories_filters_by_user(self, tmp_memories):
        await tmp_memories.store(user="alice", content="a", channel_id=1)
        await tmp_memories.store(user="bob", content="b", channel_id=1)
        items = await tmp_memories.list_memories(user="alice")
        assert len(items) == 1
        assert items[0]["user"] == "alice"

    @pytest.mark.asyncio
    async def test_forget_memory(self, tmp_memories):
        result = await tmp_memories.store(user="u", content="delete me", channel_id=1)
        rid = result["id"]
        import engine.memory_engine as me_module
        orig_get = me_module.get_engine
        me_module.get_engine = lambda: tmp_memories
        try:
            result = await me_module.execute_tool("forget_memory", {"memory_id": rid})
            assert "Forgotten" in result
            assert len(tmp_memories.memories) == 0
        finally:
            me_module.get_engine = orig_get

    @pytest.mark.asyncio
    async def test_forget_unknown(self, tmp_memories):
        import engine.memory_engine as me_module
        orig_get = me_module.get_engine
        me_module.get_engine = lambda: tmp_memories
        try:
            result = await me_module.execute_tool("forget_memory", {"memory_id": "nonexistent"})
            assert "not found" in result.lower()
        finally:
            me_module.get_engine = orig_get

    @pytest.mark.asyncio
    async def test_search_with_channel_filter(self, tmp_memories):
        await tmp_memories.store(user="u", content="fact", channel_id=1)
        results = await tmp_memories.search("fact", channel_id=1)
        assert len(results) == 1
        results_empty = await tmp_memories.search("fact", channel_id=999)
        assert len(results_empty) == 0


class TestKeywordSearch:
    def test_empty_query(self):
        assert _keyword_search("", []) == []

    def test_stop_words_filtered(self):
        results = _keyword_search("the quick brown fox", [{"content": "the quick brown fox jumped"}])
        assert len(results) == 1

    def test_fuzzy_match(self):
        results = _keyword_search("helo", [{"content": "hello world"}])
        assert len(results) == 1

    def test_no_match(self):
        # With threshold > 0, no match means empty results
        results = _keyword_search("nonexistent", [{"content": "hello world"}], threshold=0.01)
        assert results == []

    def test_threshold_zero_returns_all(self):
        # With threshold=0.0 (default), all items are returned regardless
        results = _keyword_search("xyz", [{"content": "hello world"}], threshold=0.0)
        assert len(results) == 1


class TestGetTools:
    def test_returns_three_tools(self):
        tools = get_tools()
        assert len(tools) == 3
        names = [t["function"]["name"] for t in tools]
        assert "remember" in names
        assert "recall" in names
        assert "forget_memory" in names

    def test_tool_structure(self):
        tools = get_tools()
        for t in tools:
            assert t["type"] == "function"
            assert "function" in t
            assert "name" in t["function"]
            assert "parameters" in t["function"]
