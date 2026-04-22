"""Memory engine - stores and recalls arbitrary facts and notes."""

import asyncio
import json
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

MEMORIES_FILE = Path("data/memories.json")


class Memory:
    def __init__(self, user: str, content: str, channel_id: int, author_id: int = 0):
        self.id = str(uuid.uuid4())[:8]
        self.user = user
        self.content = content
        self.channel_id = channel_id
        self.author_id = author_id
        self.created_at = datetime.now().isoformat()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user": self.user,
            "content": self.content,
            "channel_id": self.channel_id,
            "author_id": self.author_id,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict):
        m = cls(d["user"], d["content"], d["channel_id"], d.get("author_id", 0))
        m.id = d["id"]
        m.created_at = d["created_at"]
        return m


def _normalize(text: str) -> str:
    import re
    import unicodedata
    text = text.lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _keyword_search(query: str, items: list[dict], threshold: float = 0.0) -> list[dict]:
    """Fuzzy keyword search across memory content."""
    query_keywords = set(_normalize(query).split())
    if not query_keywords:
        return []

    stop_words = {
        "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "will", "would", "shall",
        "should", "may", "might", "can", "could", "to", "of", "in", "for",
        "on", "with", "at", "by", "from", "as", "into", "through", "during",
        "and", "but", "or", "nor", "not", "so", "yet", "both", "either",
        "neither", "each", "every", "all", "any", "some", "such", "no",
        "only", "same", "than", "too", "very", "just", "about", "above",
        "after", "again", "below", "between", "it", "its", "if", "then",
        "that", "this", "these", "those", "what", "which", "who", "whom",
        "when", "where", "why", "how", "my", "your", "his", "her", "our",
        "their", "me", "him", "we", "they", "am", "remember", "recall",
        "forget", "forgot", "storage", "store", "save", "know",
    }
    query_keywords = {t for t in query_keywords if len(t) >= 3 and t not in stop_words}
    if not query_keywords:
        return []

    results = []
    for item in items:
        content = _normalize(item["content"])
        words = set(content.split())
        matched = sum(
            1.0 if kw in words else (0.5 if any(kw in w or w in kw for w in words) else 0.0)
            for kw in query_keywords
        )
        score = matched / len(query_keywords)
        if score >= threshold:
            results.append((score, item))

    results.sort(key=lambda x: x[0], reverse=True)
    return [item for _, item in results]


class MemoryEngine:
    def __init__(self):
        self.memories: list[Memory] = []

    async def load(self) -> None:
        MEMORIES_FILE.parent.mkdir(parents=True, exist_ok=True)
        if MEMORIES_FILE.exists():
            try:
                data = json.loads(MEMORIES_FILE.read_text())
                self.memories = [Memory.from_dict(m) for m in data]
                logger.info("Loaded %d memories from disk", len(self.memories))
            except (json.JSONDecodeError, KeyError, TypeError) as e:
                logger.error("Failed to load memories: %s, starting fresh", e)
                self.memories = []
        else:
            MEMORIES_FILE.write_text("[]")
            self.memories = []

    async def save(self) -> None:
        MEMORIES_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = [m.to_dict() for m in self.memories]
        MEMORIES_FILE.write_text(json.dumps(data, indent=2))

    async def store(
        self, user: str, content: str, channel_id: int, author_id: int = 0
    ) -> dict:
        m = Memory(user, content, channel_id, author_id)
        self.memories.append(m)
        await self.save()
        logger.info("Memory stored: '%s' in channel %d", content, channel_id)
        return m.to_dict()

    async def search(self, query: str, threshold: float = 0.0) -> list[dict]:
        items = [m.to_dict() for m in self.memories]
        results = _keyword_search(query, items, threshold)
        logger.info("Memory search for '%s' returned %d results", query, len(results))
        return results

    async def list_memories(
        self, channel_id: Optional[int] = None, user: Optional[str] = None
    ) -> list[dict]:
        items = self.memories
        if channel_id is not None:
            items = [m for m in items if m.channel_id == channel_id]
        if user is not None:
            items = [m for m in items if m.user == user]
        return sorted(
            [m.to_dict() for m in items],
            key=lambda m: m["created_at"],
            reverse=True,
        )

    async def delete(self, memory_id: str) -> dict:
        for m in self.memories:
            if m.id == memory_id:
                self.memories.remove(m)
                await self.save()
                logger.info("Memory deleted: %s", memory_id)
                return m.to_dict()
        raise ValueError(f"Memory {memory_id} not found")


_engine = MemoryEngine()


def get_engine():
    return _engine


def get_tools() -> list:
    return [
        {
            "type": "function",
            "function": {
                "name": "remember",
                "description": "Remember a fact, note, or piece of information. Use when the user asks you to remember something. content (str) is what to remember, user (str) is the user mention.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "channel_id": {"type": "integer", "description": "Discord channel ID"},
                        "user": {"type": "string", "description": "User mention string (e.g. @username)"},
                        "content": {"type": "string", "description": "The fact or note to remember, e.g. 'server restart is at 3am Friday'"},
                    },
                    "required": ["content"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "recall",
                "description": "Search and retrieve stored memories. Use when the user asks what you know, what you remembered, or wants to recall something.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query for memories. Use any relevant terms."},
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "forget_memory",
                "description": "Forget a previously stored memory by its ID. Use when the user asks to forget, delete, or remove a memory.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "memory_id": {"type": "string", "description": "The memory ID to forget"},
                    },
                    "required": ["memory_id"],
                },
            },
        },
    ]


async def execute_tool(name: str, args: dict, author_id: int = 0) -> str:
    engine = get_engine()
    if name == "remember":
        user = args.get("user", "")
        content = args["content"]
        channel_id = args.get("channel_id", 0)
        result = await engine.store(user, content, channel_id, author_id)
        return f"I'll remember that: \"{result['content']}\" (ID: {result['id']})"
    elif name == "recall":
        query = args.get("query", "")
        if not query:
            memories = await engine.list_memories()
            if not memories:
                return "I don't remember anything yet."
            lines = ["## What I remember:"]
            for m in memories:
                lines.append(f"- {m['content']} (by {m['user']})")
            return "\n".join(lines)
        results = await engine.search(query)
        if not results:
            return f"I couldn't find any memories matching \"{query}\"."
        lines = [f"### Found {len(results)} memory(s):"]
        for m in results:
            lines.append(f"- {m['content']} (by {m['user']})")
        return "\n".join(lines)
    elif name == "forget_memory":
        try:
            m = await engine.delete(args["memory_id"])
            return f"Forgetten: \"{m['content']}\" (ID: {m['id']})"
        except ValueError as e:
            return str(e)
    return f"Unknown memory tool: {name}"
