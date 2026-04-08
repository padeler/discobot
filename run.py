import json
import logging
import discord
from discord import app_commands
import toml
import aiohttp
import re
import asyncio
from discord.ext import tasks
from pathlib import Path

# Configuration
config = toml.load("config.toml")
TOKEN = config['discord']['token']
MODEL = config['ollama']['model']
TEMPERATURE = config['ollama'].get('temperature', 0.7)
TOP_P = config['ollama'].get('top_p', 0.9)
NUM_PREDICT = config['ollama'].get('num_predict', 1024)
API_URL = 'http://localhost:11434/api/chat'
HISTORY_DIR = Path("data/history")
HISTORY_DIR.mkdir(parents=True, exist_ok=True)


# History settings
HISTORY_MAX_MESSAGES = config.get('history', {}).get('max_messages', 40)
HISTORY_CLEANUP_ENABLED = config.get('history', {}).get('cleanup_enabled', True)

# Channel response settings
CHANNEL_RESPONSE_ENABLED = config.get('channel', {}).get('enabled', True)
AUTO_RESPOND_MIN_LENGTH = config.get('channel', {}).get('min_message_length', 10)
LOOP_INTERVAL = config.get('channel', {}).get('loop_interval', 20)

# Logging setup
LOG_LEVEL = config.get('logging', {}).get('level', 'INFO').upper()
LOG_FILE = Path("data") / config.get('logging', {}).get('file', 'bot.log')


# Preprompt setup
PREPROMPT_ENABLED = config.get('preprompt', {}).get('enabled', True)
PREPROMPT_SYSTEM = config.get('preprompt', {}).get('system', "You are a friendly and helpful AI assistant on Discord.")

# Create logs directory if it doesn't exist
log_dir = LOG_FILE.parent
if not log_dir.exists():
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

def load_history(guild_id, channel_id):
    file_path = HISTORY_DIR / f"{guild_id}_{channel_id}.json"
    if file_path.exists():
        with open(file_path, 'r', encoding='utf-8') as f:
            try:
                history = json.load(f)
            except json.JSONDecodeError:
                logger.error(f"History file {file_path} is corrupted. Starting with an empty history.")
                return []
            logger.debug(f"Loaded {len(history)} messages from {file_path}")
            return history
    logger.debug(f"No existing history found for {guild_id}_{channel_id}, starting fresh")
    return []

def save_history(guild_id, channel_id, history):
    file_path = HISTORY_DIR / f"{guild_id}_{channel_id}.json"
    temp_path = file_path.with_suffix('.tmp')
    try:
        with open(temp_path, 'w', encoding='utf-8') as f:
            json.dump(history, f)
        temp_path.replace(file_path)
    except Exception as e:
        logger.error(f"Failed to save history to {file_path}: {e}")
    logger.debug(f"Saved {len(history)} messages to {file_path}")

def cleanup_history(history):
    """Keep only the most recent N messages to prevent unbounded growth."""
    if not HISTORY_CLEANUP_ENABLED or len(history) <= HISTORY_MAX_MESSAGES:
        return history
    removed = len(history) - HISTORY_MAX_MESSAGES
    history = history[-HISTORY_MAX_MESSAGES:]
    logger.debug(f"Cleaned up history: removed {removed} messages, kept {len(history)}")
    return history

def add_to_history(guild_id, channel_id, role, content):
    """Helper to add a single message to history without generating a response."""
    history = load_history(guild_id, channel_id)
    history.append({"role": role, "content": content})
    history = cleanup_history(history)
    save_history(guild_id, channel_id, history)

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


def get_context_summary_from_history(history):
    """Extract relevant context from conversation history for channel responses."""
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

# Remove global history variable as it's no longer used

intents = discord.Intents.default()
intents.message_content = True
class MyBot(discord.Client):
    def __init__(self, intents):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.response_queue = []
        self.queue_lock = asyncio.Lock()

    async def setup_hook(self):
        # This is where you sync commands
        await self.tree.sync()
        self.session = aiohttp.ClientSession()
        self.process_messages_loop.start()
        logger.info("Command tree synced, session created, and processing loop started")

    @tasks.loop(seconds=LOOP_INTERVAL)
    async def process_messages_loop(self):
        """Periodically process queued messages and generate responses."""
        try:
            async with self.queue_lock:
                if not self.response_queue:
                    return

                # Copy and clear the queue
                current_batch = list(self.response_queue)
                self.response_queue.clear()

            logger.info(f"Processing batch of {len(current_batch)} messages")

            # Group requests by channel
            channels_map = {}
            for req in current_batch:
                cid = req['channel_id']
                if cid not in channels_map:
                    channels_map[cid] = []
                channels_map[cid].append(req)

            for channel_id, requests in channels_map.items():
                # Get the channel object
                channel = self.get_channel(channel_id)
                if not channel:
                    logger.warning(f"Could not find channel {channel_id}, skipping")
                    continue

                # Evaluate the batch to decide the action
                action, target_id = await evaluate_batch(channel.name, requests)

                if action == "SKIP":
                    logger.info(f"Batch evaluator decided to SKIP for #{channel.name}")
                    continue

                # Determine the message to reply to
                reply_to_msg = None
                if action == "REPLY_MESSAGE":
                    # Find the message object in the batch that matches the ID
                    for req in requests:
                        if str(req['message'].id) == target_id:
                            reply_to_msg = req['message']
                            break
                    if not reply_to_msg:
                        logger.warning(f"LLM requested reply to {target_id} but it wasn't in the batch. Replying to channel instead.")
                        action = "REPLY_CHANNEL"

                logger.info(f"Responding to #{channel.name} with action {action}")

                try:
                    await channel.typing()
                    # The prompt for the actual response asks the AI to address the conversation.
                    # Since get_ollama_response uses the history file, it will see the whole batch.
                    generation_prompt = "Please provide a response to the recent conversation in the channel."
                    response = await get_ollama_response(
                        generation_prompt,
                        requests[0]['guild_id'],
                        requests[0]['channel_id'],
                        include_context=True
                    )
                    await send_chunked_response(channel, response, reply_to=reply_to_msg)
                    logger.info(f"Response sent in #{channel.name}")
                except Exception as e:
                    logger.error(f"Error in processing loop for channel {channel.name}: {e}")

        except Exception as e:
            logger.error(f"Critical error in process_messages_loop: {e}")

    async def close(self):
        await super().close()
        if hasattr(self, 'session'):
            await self.session.close()
            logger.info("ClientSession closed")

client = MyBot(intents=intents)


async def evaluate_batch(channel_name, requests):
    """
    Evaluate a batch of messages to decide if and how the bot should respond.
    Returns (action, target_message_id)
    """
    messages_list = []
    has_mention = False
    for req in requests:
        msg = req['message']
        messages_list.append(f"[{msg.author.display_name}]: {msg.content}")
        if req.get('is_mention'):
            has_mention = True

    batch_content = "\n".join(messages_list)

    eval_prompt = f"""You are a Discord conversation manager. Below are the messages received in channel #{channel_name} since the last response:

{batch_content}

Decision Rules:
- If the bot was mentioned, a response is highly expected unless the problem was already solved by someone else in the batch.
- RESPOND if there are questions, interesting statements, or a need for the bot's expertise.
- SKIP if the conversation is spam, gibberish, or doesn't require an AI response.

Respond ONLY with one of the following codes:
- SKIP: No response needed.
- REPLY_CHANNEL: Give a general response to the group.
- REPLY_MESSAGE:<id>: Reply specifically to a message ID.

Batch Mentioned: {has_mention}

Your decision:"""

    logger.debug(f"Evaluating batch for #{channel_name}")
    try:
        # Use a temporary history for evaluation to avoid polluting it
        test_history = [{"role": "user", "content": eval_prompt}]
        messages_for_api = build_messages_with_system(test_history)

        async with client.session.post(API_URL, json={"model": MODEL, "messages": messages_for_api, "stream": False}) as resp:
            if resp.status == 200:
                data = await resp.json()
                decision = data['message']['content'].strip()

                if decision == "SKIP":
                    return "SKIP", None
                elif decision == "REPLY_CHANNEL":
                    return "REPLY_CHANNEL", None
                elif decision.startswith("REPLY_MESSAGE:"):
                    msg_id = decision.split(":")[1].strip()
                    return "REPLY_MESSAGE", msg_id

                logger.warning(f"Unexpected decision from LLM: {decision}")
                return "SKIP", None
    except Exception as e:
        logger.error(f"Error in evaluate_batch: {e}")

    return "SKIP", None

async def get_ollama_response(prompt, guild_id, channel_id, include_context=False):
    """
    Get response from Ollama API with optional conversation context.

    Args:
        prompt: The user's message/prompt
        guild_id: The ID of the server
        channel_id: The ID of the channel
        include_context: Whether to include recent conversation summary
    """
    # Load history once and reuse it
    history = load_history(guild_id, channel_id)

    # Build the message with optional context from history
    context = get_context_summary_from_history(history) if include_context else ""
    full_prompt = f"{context}{prompt}"

    logger.debug(f"Prompt ({'with context' if include_context else 'no context'}): {full_prompt[:100]}...")

    # NOTE: User messages are now added to history in on_message.
    # We only need to add the assistant response here.

    # Build messages with system prompt for API request
    messages_for_api = build_messages_with_system(history)

    try:
        async with client.session.post(API_URL, json={"model": MODEL, "messages": messages_for_api, "stream": False}) as resp:
            if resp.status == 200:
                data = await resp.json()
                reply = data['message']['content']

                # Add assistant response to history
                history.append({"role": "assistant", "content": reply})
                history = cleanup_history(history)
                save_history(guild_id, channel_id, history)

                logger.info(f"Response: {reply[:100]}...")
                return reply
            logger.error(f"Ollama API error: {resp.status}")
            return "⚠️ I'm having trouble connecting to my brain (Ollama API error)."
    except Exception as e:
        logger.error(f"Ollama connection error: {e}")
        return "⚠️ I'm currently unable to reach the AI server. Please try again in a moment."

async def should_auto_respond(message_content, channel_name, guild_id, channel_id):
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
        # Use a separate request that doesn't modify history
        test_history = load_history(guild_id, channel_id)[-10:]  # Only recent context for evaluation
        test_history.append({"role": "user", "content": eval_prompt})
        messages_for_api = build_messages_with_system(test_history)

        async with client.session.post(API_URL, json={"model": MODEL, "messages": messages_for_api, "stream": False, "options": {"temperature": TEMPERATURE, "top_p": TOP_P, "num_predict": NUM_PREDICT}}) as resp:
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
    guild_id = interaction.guild_id if interaction.guild else None
    channel_id = interaction.channel_id

    if guild_id:
        file_path = HISTORY_DIR / f"{guild_id}_{channel_id}.json"
        if file_path.exists():
            file_path.unlink()
        await interaction.response.send_message(f"🧹 Conversation history for this channel has been cleared!")
    else:
        await interaction.response.send_message("❌ This command can only be used in a server.")

@client.tree.command(name="set_min_length", description="Set the minimum message length for auto-response")
@app_commands.describe(length="Minimum characters required")
async def set_min_length(interaction: discord.Interaction, length: int):
    global AUTO_RESPOND_MIN_LENGTH
    if length < 0:
        await interaction.response.send_message("❌ Length cannot be negative.", ephemeral=True)
        return

    AUTO_RESPOND_MIN_LENGTH = length

    # Persist to config.toml
    try:
        config_path = Path("config.toml")
        if config_path.exists():
            current_config = toml.load(config_path)
            if 'channel' not in current_config:
                current_config['channel'] = {}
            current_config['channel']['min_message_length'] = length
            with open(config_path, 'w', encoding='utf-8') as f:
                toml.dump(current_config, f)
            logger.info(f"Updated min_message_length to {length} in config.toml")
    except Exception as e:
        logger.error(f"Failed to persist min_message_length to config.toml: {e}")
        await interaction.response.send_message(f"✅ Length set to `{length}`, but failed to save to config file.", ephemeral=True)
        return

    await interaction.response.send_message(f"✅ Minimum auto-response length set to `{length}` characters and saved.")

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
    # Extract IDs for history tracking
    guild_id = message.guild.id if message.guild else None
    channel_id = message.channel.id

    logger.debug(f"Message received from {message.author.display_name} in #{message.channel.name}: {message.content[:50]}...")

    # Skip bot messages and self-messages
    if message.author.bot:
        logger.debug(f"Skipping bot message from {message.author.display_name}")
        return
    if message.author == client.user:
        logger.debug("Skipping self-message")
        return

    # Update history immediately so the bot has context of all messages
    # We use a simple formatted string for history
    history_content = f"{message.author.display_name} says: {message.content}"
    add_to_history(guild_id, channel_id, "user", history_content)

    # Check if bot is mentioned (takes priority)
    is_mentioned = client.user.mentioned_in(message) or is_bot_mentioned(message.content)

    if is_mentioned:
        logger.info(f"[MENTION] {message.author.display_name} mentioned bot in #{message.channel.name}")
        # Handle direct mention - enqueue for response
        async with client.queue_lock:
            client.response_queue.append({
                "message": message,
                "timestamp": message.created_at.timestamp(),
                "channel_id": channel_id,
                "guild_id": guild_id,
                "is_mention": True
            })
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
        should_respond, reason = await should_auto_respond(message.content, message.channel.name, guild_id, channel_id)

        if should_respond:
            logger.info(f"[AUTO-RESPOND] Will respond in #{message.channel.name}: {reason}")
            async with client.queue_lock:
                client.response_queue.append({
                    "message": message,
                    "timestamp": message.created_at.timestamp(),
                    "channel_id": channel_id,
                    "guild_id": guild_id,
                    "is_mention": False
                })
        else:
            logger.debug(f"[AUTO-RESPOND] Skipping message in #{message.channel.name}: {reason}")

client.run(TOKEN)
