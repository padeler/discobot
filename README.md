# Simple Discord AI

An AI chatbot for Discord that integrates with Ollama for local LLM inference. It supports direct mentions and auto-response channel participation.

## Features

- **Direct mentions**: Tag the bot (`@botname`) for an immediate response
- **Channel auto-response**: The bot evaluates channel messages and joins conversations when appropriate using a secondary LLM call
- **Conversation memory**: Persists per-channel history with timestamps and usernames for context-aware responses
- **Active conversation tracking**: Recognizes ongoing conversations and prioritizes users it has recently spoken with
- **Configurable behavior**: Customize all settings via `config.yaml`
- **Slash commands**: Built-in `/status`, `/clear_history`, and `/set_min_length` commands

## Installation

### Local Installation

1. [Install Ollama](https://ollama.com/download/)
2. Pull a model: `ollama pull gemma4` (or your preferred model)
3. Install dependencies: `pip install -r requirements.txt`
4. Copy `config.yaml` and set your Discord token:
   - Set the `DISCORD_TOKEN` environment variable: `export DISCORD_TOKEN="your-token"`
   - (Alternatively, create a `.env` file with `DISCORD_TOKEN="your-token"`)
5. Run: `python run.py`


## Configuration

Edit `config.yaml`:

```yaml
ollama:
  model: "gemma4-128k"
  api_url: "http://localhost:11434/api/chat"

history:
  max_messages: 40
  cleanup_enabled: true

channel:
  enabled: true              # Enable auto-response to channel messages
  min_message_length: 10     # Minimum message length to consider
  loop_interval: 1           # Batch processing interval (seconds)

conversation:
  window_minutes: 5          # Time window for active conversations
  max_users_tracked: 50      # Max users to track per channel

preprompt:
  enabled: true              # Enable system prompt
  system: |
    You are a friendly and helpful AI assistant on Discord. Your name is Mech Knight.
    You engage in casual conversations, answer questions, and provide useful information.
    You don't like to talk a lot and answer only when asked or when it make sense to respond.
    Sometimes people in the chat talk in greeklish. This is normal try to reply in English or Greek.
    Be concise and brief unless asked otherwise. Be funny and relaxed.

logging:
  level: "DEBUG"
  file: "bot.log"
```

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
