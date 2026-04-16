# Discord Bot Implementation Design

**Date:** 2026-04-16
**Status:** Approved

## Summary

Implement a single-file Discord bot in `run.py` that integrates with Ollama for local LLM inference. The bot supports direct mentions and auto-response channel participation with conversation memory.

## Architecture

**Single file** (`run.py`) with clear internal modules organized as classes/functions:

### Config Loading
- Parse `config.yaml` into a `Config` dataclass
- Support `DISCORD_TOKEN` via env var or `.env` file (python-dotenv)

### OllamaClient
- Raw aiohttp HTTP calls to configured Ollama API endpoint
- Request: system prompt + conversation history + user message
- Response: stream text chunks, collect into final string

### HistoryManager
- Per-guild/channel JSON files in `data/history/`
- Time-based cleanup for max_messages
- Migration support for old format

### Message Processing
- `on_message` event queues messages into `asyncio.Queue`
- Periodic loop processes queued messages
- Two-tier response system:
  1. **Mention** → LLM call → reply (threaded)
  2. **Channel** → evaluator LLM call → decide → respond

### Slash Commands
- `/status` — model, latency, auto-response status
- `/clear_history` — clear current channel history
- `/set_min_length <n>` — update config and persist to `config.yaml`

## Key Decisions
- **LLM client:** aiohttp (raw HTTP, async)
- **Response method:** reply for mentions, channel send for auto-response
- **History format:** per-guild/channel JSON with role/content/timestamp/author fields
- **Message handling:** async queue with periodic loop

## Dependencies
- discord.py 2.7.1
- aiohttp 3.13.5
- pyyaml 6.0.2
- python-dotenv 1.2.2
