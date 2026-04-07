# Discord Bot

An AI chatbot for Discord that utilizes Ollama for local LLM inference.

## Features

- **Direct mentions**: Responds when you tag the bot (@botname)
- **Channel auto-response**: Can join conversations in channels when it detects interesting topics
- **Conversation memory**: Maintains conversation history in `history.json` for context-aware responses
- **Configurable behavior**: Customize auto-response settings via `config.toml`

## Installation

1. [Install Ollama](https://ollama.com/download/)
2. Pull a model: `ollama pull gemma4` (or your preferred model)
3. Install dependencies: `pip install discord.py aiohttp toml`
4. Copy `config.example.toml` to `config.toml` and configure:
   - Add your Discord bot token
   - Set your Ollama model
   - Adjust channel response settings
5. Run: `python run.py`

## Configuration

```toml
[discord]
token = "YOUR_DISCORD_BOT_TOKEN_HERE"

[ollama]
model = "gemma4"

[history]
max_messages = 40
cleanup_enabled = true

[channel]
enabled = true              # Enable auto-response to channel messages
min_message_length = 10     # Minimum message length to consider

[preprompt]
enabled = true              # Enable system prompt
system = """You are a friendly AI assistant on Discord.
You engage in casual conversations and answer questions.
Be concise and use a conversational tone."""
```

## Usage

- **Direct mention**: Tag the bot (`@botname`) to get an immediate response
- **Channel participation**: With auto-response enabled, the bot will evaluate channel messages and join conversations when appropriate
