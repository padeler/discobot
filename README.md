# Simple Discord AI

An AI chatbot for Discord that integrates with Ollama for local LLM inference. It supports direct mentions, auto-response channel participation, reminders, and MCP-based tool use.

The bot receives messages and places them in a queue. A background loop runs at a configurable interval and processes all queued messages. Messages are stored in per-channel history with timestamps and usernames. The bot is aware of the current date and time. All actions are logged at the configured level. Configuration is stored in `config.yaml`.

## Features

- **Direct mentions**: Tag the bot (`@botname`) for an immediate response
- **Typing indicator**: Shows "typing..." while generating responses
- **Channel auto-response**: Evaluates channel messages with a secondary LLM call and joins conversations when appropriate
- **Conversation memory**: Persists per-guild, per-channel history as JSON files in `data/history/`
- **Active conversation tracking**: Prioritizes users who have recently interacted with the bot
- **Configurable behavior**: Customize all settings via `config.yaml`
- **Slash commands**:
  - `/status` - Show bot model, latency, auto-response status, and conversation window
  - `/clear_history` - Clear conversation history for the current channel
  - `/set_min_length` - Set minimum message length for auto-response (persists to config)


## Installation

### Local Installation

1. [Install Ollama](https://ollama.com/download/)
2. Pull a model: `ollama pull gemma4` (or your preferred model)
3. Install dependencies: `pip install -r requirements.txt`
4. Copy `config.yaml` and set your Discord token:
   - Set the `DISCORD_TOKEN` environment variable: `export DISCORD_TOKEN="your-token"`
   - (Alternatively, create a `.env` file with `DISCORD_TOKEN="your-token"`)
5. Run: `python run.py`



## Slash Commands

| Command | Description |
|---------|-------------|
| `/status` | Show bot model, latency, auto-response status, and conversation window |
| `/clear_history` | Clear conversation history for the current channel |
| `/set_min_length <n>` | Set the minimum message length (in characters) for auto-response. Persists to `config.yaml` |

## Usage

- **Direct mention**: Tag the bot (`@botname`) to get a response
- **Channel participation**: With auto-response enabled, the bot evaluates channel messages using a secondary LLM call and joins conversations when appropriate
- **Active conversations**: Users who have recently exchanged messages with the bot are prioritized for continued interaction
- **History**: Messages are persisted per-guild per-channel in `data/history/` as JSON files

## Architecture

```
Message → Queue → Loop
                        ├── Mention → LLM → Reply (threaded)
                        └── Auto  → Evaluator → LLM → Response
```

All messages are queued by `on_message` and processed in a periodic loop. Mentions are handled first (highest priority), followed by auto-response evaluation for non-mention messages.

## Ollama Integration

The bot communicates with the Ollama API at the configured endpoint. Supports any model pulled via Ollama. Configure in the `ollama` section of `config.yaml`.
