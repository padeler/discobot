# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Simple Discord AI is a Python Discord bot that integrates with Ollama for local LLM inference. It supports two interaction modes:
- **Direct mentions**: Responds when the bot is tagged (@botname)
- **Channel auto-response**: Evaluates and joins conversations in channels based on message relevance

## Architecture

### Core Components

- `run.py`: Main entry point containing the Discord client, event handlers, and LLM integration
- `config.toml`: TOML configuration file for Discord token, Ollama model, and bot behavior
- `history.json`: Persistent conversation history file (created at runtime)
- `bot.log`: Application logs (created at runtime)

### Key Design Patterns

1. **Enhanced History Format**: Each history entry contains:
   - `role`: 'user' or 'assistant'
   - `content`: Message text
   - `timestamp`: ISO format timestamp for time-based filtering
   - `author`: Username (for users) or 'bot' (for assistant)

2. **Active Conversation Tracking**: Bot maintains a list of users it has recently spoken with (within configurable time window). These users are prioritized for auto-response to maintain conversation flow.

3. **Two-tier Response System**:
   - Mention-triggered responses (highest priority, always responds)
   - Auto-response evaluation using a separate LLM call to determine if a channel message deserves a response

4. **Chunked Message Sending**: Responses exceeding 2000 characters are split into multiple messages while maintaining reply threading

### Data Flow

```
Message Received → Skip bot/self → Check mention → [Mention: respond immediately]
                                              → [No mention: auto-response evaluation]
                                                              → [Respond: LLM with context]
                                                              → [Skip: log reason]
```

## Dependencies

- `discord.py`: Discord API client
- `aiohttp`: Async HTTP for Ollama API calls
- `toml`: Configuration file parsing

Install with: `pip install -r requirements.txt`

## Configuration

The bot reads from `config.toml` (copy from `config.example.toml`):

- `[discord]`: Bot token
- `[ollama]`: Model name (e.g., `gemma4`, `llama3.2`), temperature, top_p, num_predict
- `[history]`: Max messages retained, cleanup toggle
- `[channel]`: Auto-response enable/disable, minimum message length, loop interval
- `[conversation]`: Active conversation window (minutes), max users tracked
- `[logging]`: Log level and file path
- `[preprompt]`: System prompt to set bot character/personality (enabled/disabled, system message)

## Ollama Integration

- Default API endpoint: `http://localhost:11434/api/chat`
- Supports any Ollama-pulled model configured in `config.toml`
- Custom models can be created with extended context (128k) using the `ollama/` modelfiles

## Development Notes

- The bot requires `message_content` intent enabled via `discord.Intents.default()` with `intents.message_content = True`
- Auto-response evaluation uses a temporary history copy to avoid polluting conversation history
- The bot skips: its own messages, other bots' messages, DMs (for auto-response), and messages under minimum length threshold
- History files are automatically migrated from old format (embedded usernames in content) to new format (structured fields with timestamps)
