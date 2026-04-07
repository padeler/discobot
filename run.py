import json
import os
import logging
import discord
from discord import app_commands
import toml
import aiohttp
import re
from pathlib import Path

# Configuration
config = toml.load("config.toml")
TOKEN = config['discord']['token']
MODEL = config['ollama']['model']
TEMPERATURE = config['ollama'].get('temperature', 0.7)
TOP_P = config['ollama'].get('top_p', 0.9)
NUM_PREDICT = config['ollama'].get('num_predict', 1024)
API_URL = 'http://localhost:11434/api/chat'
HISTORY_FILE = "history.json"

# History settings
HISTORY_MAX_MESSAGES = config.get('history', {}).get('max_messages', 40)
HISTORY_CLEANUP_ENABLED = config.get('history', {}).get('cleanup_enabled', True)

# Channel response settings
CHANNEL_RESPONSE_ENABLED = config.get('channel', {}).get('enabled', True)
AUTO_RESPOND_MIN_LENGTH = config.get('channel', {}).get('min_message_length', 10)

# Logging setup
LOG_LEVEL = config.get('logging', {}).get('level', 'INFO').upper()
LOG_FILE = config.get('logging', {}).get('file', 'bot.log')

# Preprompt setup
PREPROMPT_ENABLED = config.get('preprompt', {}).get('enabled', True)
PREPROMPT_SYSTEM = config.get('preprompt', {}).get('system',
    "You are a friendly and helpful AI assistant on Discord. "
    "You engage in casual conversations, answer questions, and provide useful information. "
    "Be concise and brief unless asked otherwise. Use a conversational tone.")

# Create logs directory if it doesn't exist
log_dir = Path(LOG_FILE).parent
if log_dir and not log_dir.exists():
    log_dir.mkdir(parents=True, exist_ok=True)

# Configure logging
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.StreamHandler(),  # Console output
        logging.FileHandler(LOG_FILE, encoding='utf-8')  # File output
    ]
)
logger = logging.getLogger('DiscordBot')

def load_history():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, 'r') as f:
            try:
                history = json.load(f)
            except json.JSONDecodeError:
                logger.error("History file is corrupted. Starting with an empty history.")
                return []
            logger.debug(f"Loaded {len(history)} messages from history")
            return history
    logger.debug("No existing history found, starting fresh")
    return []

def save_history(history):
    with open(HISTORY_FILE, 'w') as f:
        json.dump(history, f)
    logger.debug(f"Saved {len(history)} messages to history")

def cleanup_history(history):
    """Keep only the most recent N messages to prevent unbounded growth."""
    if not HISTORY_CLEANUP_ENABLED or len(history) <= HISTORY_MAX_MESSAGES:
        return history
    removed = len(history) - HISTORY_MAX_MESSAGES
    history = history[-HISTORY_MAX_MESSAGES:]
    logger.debug(f"Cleaned up history: removed {removed} messages, kept {len(history)}")
    return history

def get_bot_id():
    """Get the bot's user ID for mention detection."""
    return client.user.id if client.user else None

def is_bot_mentioned(content):
    """Check if the bot is mentioned in the message content."""
    bot_id = get_bot_id()
    if not bot_id:
        return False
    # Check for @mention format: <@userid> or <@!userid>
    bot_mention_pattern = rf'<@!?{bot_id}>'
    return bool(re.search(bot_mention_pattern, content))

def build_messages_with_system(user_messages):
    """Prepend system prompt to messages if enabled."""
    if not PREPROMPT_ENABLED:
        return user_messages
    return [{"role": "system", "content": PREPROMPT_SYSTEM}] + user_messages


def get_context_summary():
    """Extract relevant context from conversation history for channel responses."""
    history = load_history()
    if not history:
        return ""

    # Get the last few assistant responses to understand recent topics
    recent_topics = []
    for msg in reversed(history[-10:]):
        if msg['role'] == 'assistant' and len(recent_topics) < 3:
            # Get first sentence or short summary
            content = msg['content'][:200]
            if '.' in content:
                content = content.split('.')[0] + '.'
            recent_topics.append(content)

    if recent_topics:
        return "Recent conversation topics: " + " ".join(recent_topics) + "\n\n"
    return ""

history = load_history()

intents = discord.Intents.default()
intents.message_content = True
class MyBot(discord.Client):
    def __init__(self, intents):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        # This is where you sync commands
        await self.tree.sync()
        logger.info("Command tree synced")

client = MyBot(intents=intents)


async def get_ollama_response(prompt, include_context=False):
    """
    Get response from Ollama API with optional conversation context.

    Args:
        prompt: The user's message/prompt
        include_context: Whether to include recent conversation summary
    """
    global history

    # Build the message with optional context from history
    context = get_context_summary() if include_context else ""
    full_prompt = f"{context}{prompt}"

    logger.info(f"Prompt ({'with context' if include_context else 'no context'}): {full_prompt[:100]}...")
    history.append({"role": "user", "content": full_prompt})
    history = cleanup_history(history)
    save_history(history)

    # Build messages with system prompt for API request
    messages_for_api = build_messages_with_system(history)

    async with aiohttp.ClientSession() as session:
        async with session.post(API_URL, json={"model": MODEL, "messages": messages_for_api, "stream": False}) as resp:
            if resp.status == 200:
                data = await resp.json()
                reply = data['message']['content']
                history.append({"role": "assistant", "content": reply})
                history = cleanup_history(history)
                save_history(history)
                logger.info(f"Response: {reply[:100]}...")
                return reply
            logger.error(f"Ollama API error: {resp.status}")
            return f"Error: Ollama API returned {resp.status}"

async def should_auto_respond(message_content, channel_name):
    """
    Use the LLM to evaluate if a message is interesting enough to warrant a response.
    Returns (should_respond: bool, reason: str)
    """
    eval_prompt = f"""You are a helpful Discord bot that can join conversations.
Evaluate if the following message deserves a response.

Message from #{channel_name}: "{message_content}"

Respond with only two lines:
1. "RESPOND" or "SKIP"
2. A brief reason (one sentence)

Rules:
- RESPOND to questions, interesting statements, or conversation starters
- RESPOND if you have relevant context from previous conversations
- SKIP if it's clearly spam, gibberish, or a private conversation between others
- SKIP if it's too short (< 5 words) and has no clear meaning

Your evaluation:"""

    logger.debug(f"Evaluating auto-response for message in #{channel_name}")
    try:
        async with aiohttp.ClientSession() as session:
            # Use a separate request that doesn't modify history
            test_history = load_history()[-10:]  # Only recent context for evaluation
            test_history.append({"role": "user", "content": eval_prompt})
            messages_for_api = build_messages_with_system(test_history)

            async with session.post(API_URL, json={"model": MODEL, "messages": messages_for_api, "stream": False, "options": {"temperature": TEMPERATURE, "top_p": TOP_P, "num_predict": NUM_PREDICT}}) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    evaluation = data['message']['content']
                    lines = evaluation.strip().split('\n')
                    should_respond = lines[0].upper() == "RESPOND" if lines else False
                    reason = lines[1] if len(lines) > 1 else "No reason given"
                    logger.debug(f"Auto-response evaluation: {'RESPOND' if should_respond else 'SKIP'} - {reason}")
                    return should_respond, reason
    except Exception as e:
        logger.error(f"Error in should_auto_respond: {e}")

    return False, "Error evaluating message"

async def send_chunked_response(channel, content, reply_to=None):
    """Send a response in chunks of 2000 characters, replying to a specific message if needed."""
    for i in range(0, len(content), 2000):
        chunk = content[i:i+2000]
        kwargs = {"content": chunk}
        if reply_to and i == 0:  # Only set reply on first chunk
            kwargs["reference"] = reply_to
        await channel.send(**kwargs)

@client.tree.command(name="status", description="Check the bot's status")
async def status(interaction: discord.Interaction):
    latency = round(client.latency * 1000)
    await interaction.response.send_message(
        f"🤖 **Bot Status**\n"
        f"**Model:** `{MODEL}`\n"
        f"**Latency:** `{latency}ms`\n"
        f"**Auto-response:** {'Enabled' if CHANNEL_RESPONSE_ENABLED else 'Disabled'}"
    )

@client.tree.command(name="clear_history", description="Clear the conversation history")
async def clear_history(interaction: discord.Interaction):
    global history
    if os.path.exists(HISTORY_FILE):
        os.remove(HISTORY_FILE)
    history = []
    await interaction.response.send_message("🧹 Conversation history has been cleared!")

@client.tree.command(name="set_min_length", description="Set the minimum message length for auto-response")
@app_commands.describe(length="Minimum characters required")
async def set_min_length(interaction: discord.Interaction, length: int):
    global AUTO_RESPOND_MIN_LENGTH
    if length < 0:
        await interaction.response.send_message("❌ Length cannot be negative.", ephemeral=True)
        return
    AUTO_RESPOND_MIN_LENGTH = length
    await interaction.response.send_message(f"✅ Minimum auto-response length set to `{length}` characters.")

@client.event
async def on_ready():
    logger.info(f"Logged in as {client.user}")
    logger.info(f"Bot ID: {client.user.id}")
    logger.info(f"Channel response enabled: {CHANNEL_RESPONSE_ENABLED}")
    logger.info(f"Log file: {LOG_FILE}")
    # Syncing commands can take a few seconds
    await client.tree.sync()
    logger.info("Command tree synced")

@client.event
async def on_message(message):
    logger.debug(f"Message received from {message.author.display_name} in #{message.channel.name}: {message.content[:50]}...")

    # Skip bot messages and self-messages
    if message.author.bot:
        logger.debug(f"Skipping bot message from {message.author.display_name}")
        return
    if message.author == client.user:
        logger.debug("Skipping self-message")
        return

    # Check if bot is mentioned (takes priority)
    is_mentioned = client.user.mentioned_in(message) or is_bot_mentioned(message.content)

    if is_mentioned:
        logger.info(f"[MENTION] {message.author.display_name} mentioned bot in #{message.channel.name}")
        # Handle direct mention - respond in the channel
        prompt = f"{message.author.display_name} says: {message.content}"
        try:
            await message.channel.typing()
            response = await get_ollama_response(prompt, include_context=True)
            logger.info(f"[MENTION] Responding to {message.author.display_name} in #{message.channel.name}")
            await send_chunked_response(message.channel, response, reply_to=message)
            logger.info(f"[MENTION] Response sent: {response[:100]}...")
        except discord.Forbidden:
            logger.error(f"Permission denied in {message.channel.name}")
        except Exception as e:
            logger.error(f"Error sending response: {e}")
        return

    # Channel auto-response (only if enabled and not a mention)
    if CHANNEL_RESPONSE_ENABLED:
        # Skip DMs for auto-response
        if isinstance(message.channel, discord.DMChannel):
            logger.debug("Skipping DM channel for auto-response")
            return

        # Skip very short messages
        if len(message.content.strip()) < AUTO_RESPOND_MIN_LENGTH:
            logger.debug(f"Skipping short message ({len(message.content.strip())} chars)")
            return

        # Skip if message is only mentions or links
        cleaned = re.sub(r'<@!?\d+>', '', message.content).strip()
        if not cleaned or len(cleaned) < 5:
            logger.debug("Skipping message with only mentions/links")
            return

        # Evaluate if we should respond
        should_respond, reason = await should_auto_respond(message.content, message.channel.name)

        if should_respond:
            logger.info(f"[AUTO-RESPOND] Will respond in #{message.channel.name}: {reason}")
            prompt = f"Context: This is a channel message (not a direct mention). {message.author.display_name} says: {message.content}"
            try:
                await message.channel.typing()
                response = await get_ollama_response(prompt, include_context=True)
                logger.info(f"[AUTO-RESPOND] Responding in #{message.channel.name}")
                await send_chunked_response(message.channel, response, reply_to=message)
                logger.info(f"[AUTO-RESPOND] Response sent: {response[:100]}...")
            except discord.Forbidden:
                logger.error(f"Permission denied in {message.channel.name}")
            except Exception as e:
                logger.error(f"Error sending auto-response: {e}")
        else:
            logger.debug(f"[AUTO-RESPOND] Skipping message in #{message.channel.name}: {reason}")

client.run(TOKEN)
