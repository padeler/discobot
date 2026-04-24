# Simple Discord AI

An AI chatbot for Discord that integrates with Ollama for local LLM inference. It supports direct mentions, auto-response channel participation, reminders, and MCP-based tool use.

The bot receives messages and places them in a queue. A background loop runs at a configurable interval and processes all queued messages. Messages are stored in per-channel history with timestamps and usernames. The bot is aware of the current date and time. All actions are logged at the configured level. Configuration is stored in `config.yaml`.

## Features

- **Direct mentions**: Tag the bot (`@botname`) for an immediate response
- **Typing indicator**: Shows "typing..." while generating responses
- **Channel auto-response**: Evaluates channel messages with a secondary LLM call and joins conversations when appropriate
- **Conversation memory**: Persists per-guild, per-channel history as JSON files in `data/history/`
- **Active conversation tracking**: Prioritizes users who have recently interacted with the bot
- **MCP tool integration**: Web search (Tavily), URL fetching, reminders, and persistent memory
- **Configurable behavior**: Customize all settings via `config.yaml`
- **Slash commands**:
  - `/status` - Show bot model, auto-response status, and conversation window
  - `/clear_history` - Clear conversation history for the current channel
  - `/set_min_length` - Set minimum message length for auto-response (persists to config)
  - `/skills` - List all available skills
  - `/show_preprompt` - Show the current preprompt
  - `/set_preprompt` - Set a new preprompt (persists to config)


## Installation

### Prerequisites

- **Python 3.12+**
- **Node.js 18+** (for MCP server dependencies)
- **npm** (bundled with Node.js)
- **Ollama** running with a compatible model

### Local Installation

1. [Install Ollama](https://ollama.com/download/)
2. Pull a model: `ollama pull gemma4` (or your preferred model)
3. Set up a Python virtual environment:
   ```bash
   python -m venv pyenv
   source pyenv/bin/activate
   ```
4. Install Python dependencies:
   ```bash
   pip install -r requirements.txt
   ```
5. Install MCP server dependencies:
   ```bash
   npx -y tavily-mcp  # Verifies tavily-search works
   npx -y mcp-fetch-server  # Verifies fetch works
   ```
6. Configure the bot:
   - Copy `config.yaml` and edit as needed
   - Set the `DISCORD_TOKEN` environment variable:
     ```bash
     export DISCORD_TOKEN="your-token"
     ```
   - (Alternatively, create a `.env` file with `DISCORD_TOKEN="your-token"`)
7. Run: `python run.py`

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DISCORD_TOKEN` | Yes | Your Discord bot token |
| `TAVILY_API_KEY` | If MCP enabled | API key for Tavily web search. Get one at [tavily.com](https://www.tavily.com/) |

These can be set in a `.env` file in the project root or exported in your shell before running the bot.

## Slash Commands

| Command | Description |
|---------|---------|
| `/status` | Show bot model, auto-response status, and conversation window |
| `/clear_history` | Clear conversation history for the current channel |
| `/set_min_length <n>` | Set the minimum message length (in characters) for auto-response. Persists to `config.yaml` |
| `/skills` | List all available skills |
| `/show_preprompt` | Show the current preprompt |
| `/set_preprompt <text>` | Set a new preprompt. Persists to `config.yaml` |

## Usage

- **Direct mention**: Tag the bot (`@botname`) to get a response
- **Channel participation**: With auto-response enabled, the bot evaluates channel messages using a secondary LLM call and joins conversations when appropriate
- **Active conversations**: Users who have recently exchanged messages with the bot are prioritized for continued interaction
- **History**: Messages are persisted per-guild per-channel in `data/history/` as JSON files

## Architecture

```
Message → on_message → Priority Queue (mentions=0, auto=1)
                                │
                    Processor Loop (async task, single-threaded)
                                ├── Mentions → LLM → Reply
                                └── Auto  → Evaluator (with history context) → LLM → Response
```

All messages are enqueued by `on_message` (fast, never blocks). A background async loop drains the queue and processes messages serially, eliminating race conditions on shared state. Mentions are processed immediately; non-mention messages are evaluated for auto-response with full conversation context.

For detailed architecture, see [`MESSAGE_HANDLING.md`](MESSAGE_HANDLING.md).

## Ollama Integration

The bot communicates with the Ollama API at the configured endpoint. Supports any model pulled via Ollama. Configure in the `ollama` section of `config.yaml`.
