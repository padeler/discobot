# Simple Discord AI

An AI chatbot for Discord that utilizes Ollama for local LLM inference.

## Features

- **Direct mentions**: Responds when you tag the bot (@botname)
- **Channel auto-response**: Can join conversations in channels when it detects interesting topics
- **Conversation memory**: Maintains conversation history with timestamps and usernames for context-aware responses
- **Active conversation tracking**: Recognizes ongoing conversations and prioritizes users it has recently spoken with
- **Configurable behavior**: Customize all settings via `config.toml`

## Installation

### Local Installation

1. [Install Ollama](https://ollama.com/download/)
2. Pull a model: `ollama pull gemma4` (or your preferred model)
3. Install dependencies: `pip install -r requirements.txt`
4. Copy `config.example.toml` to `config.toml` and configure:
   - Add your Discord bot token
   - Set your Ollama model
   - Adjust channel response settings
5. Run: `python run.py`

### Docker Installation

1. Set your Discord token as an environment variable:
   ```bash
   export DISCORD_TOKEN="your-discord-bot-token-here"
   ```

2. Build and run with Docker Compose:
   ```bash
   docker-compose up --build
   ```

3. Or build and run manually:
   ```bash
   docker build -t discord-ai .
   docker run -e DISCORD_TOKEN="$DISCORD_TOKEN" --name discord-ai discord-ai
   ```

The Docker container includes:
- Python 3.12 with all dependencies
- Ollama server running in the background
- The gemma4-128k model (pulled from the base gemma4)

## Configuration

```toml
[discord]
token = "YOUR_DISCORD_BOT_TOKEN_HERE"

[ollama]
model = "gemma4-128k"

[history]
max_messages = 40
cleanup_enabled = true

[channel]
enabled = true              # Enable auto-response to channel messages
min_message_length = 10     # Minimum message length to consider
loop_interval = 20          # Batch processing interval (seconds)

[conversation]
window_minutes = 5          # Time window for active conversations
max_users_tracked = 50      # Max users to track per channel

[preprompt]
enabled = true              # Enable system prompt
system = """You are a friendly AI assistant on Discord.
You engage in casual conversations and answer questions.
Be concise and use a conversational tone."""
```

## Usage

- **Direct mention**: Tag the bot (`@botname`) to get an immediate response
- **Channel participation**: With auto-response enabled, the bot will evaluate channel messages and join conversations when appropriate
- **Active conversations**: Users who have recently exchanged messages with the bot will be prioritized for continued interaction
