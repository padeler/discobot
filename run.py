import json
import logging
import os
import discord
from discord import app_commands
import toml
import aiohttp
import re
import asyncio
from datetime import datetime, timedelta
from discord.ext import tasks
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()  # Load environment variables from .env file if it exists

TOKEN = os.getenv(
    "DISCORD_TOKEN", None
)  # Ensure the token is loaded into the environment

if not TOKEN:
    raise EnvironmentError(
        "DISCORD_TOKEN environment variable not set. Please set it in your environment or in config.toml"
    )

# Configuration
config = toml.load("config.toml")
MODEL = config['ollama']['model']
TEMPERATURE = config['ollama'].get('temperature', 0.7)
TOP_P = config['ollama'].get('top_p', 0.9)
NUM_PREDICT = config['ollama'].get('num_predict', 1024)
API_URL = config["ollama"]["api_url"]
HISTORY_DIR = Path("data/history")
HISTORY_DIR.mkdir(parents=True, exist_ok=True)


# History settings
HISTORY_MAX_MESSAGES = config.get('history', {}).get('max_messages', 40)
HISTORY_CLEANUP_ENABLED = config.get('history', {}).get('cleanup_enabled', True)

# Conversation tracking settings
CONVERSATION_WINDOW_MINUTES = config.get("conversation", {}).get("window_minutes", 5)
CONVERSATION_MAX_USERS_TRACKED = config.get("conversation", {}).get(
    "max_users_tracked", 50
)

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
    """Load conversation history with timestamps and usernames."""
    file_path = HISTORY_DIR / f"{guild_id}_{channel_id}.json"
    if file_path.exists():
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
                if not content.strip():
                    logger.warning(
                        f"History file {file_path} is empty. Starting with an empty history."
                    )
                    return []
                history = json.loads(content)
                if not isinstance(history, list):
                    logger.error(
                        f"History file {file_path} contains invalid format. Starting fresh."
                    )
                    return []
                # Migrate old format entries (without timestamp/author) to new format
                migrated = False
                for i, msg in enumerate(history):
                    if not isinstance(msg, dict):
                        history[i] = {
                            "role": "unknown",
                            "content": str(msg),
                            "timestamp": datetime.now().isoformat(),
                            "author": None,
                        }
                        migrated = True
                    elif "timestamp" not in msg:
                        # Old format - add current timestamp and extract author from content
                        author = None
                        if msg.get("role") == "user" and msg.get(
                            "content", ""
                        ).startswith("["):
                            match = re.match(r"\[([^]]+)\]:", msg["content"])
                            if match:
                                author = match.group(1)
                                msg["content"] = (
                                    msg["content"].split("]: ", 1)[1]
                                    if "]:" in msg["content"]
                                    else msg["content"]
                                )
                        msg["timestamp"] = datetime.now().isoformat()
                        msg["author"] = author
                        migrated = True
                if migrated:
                    logger.info(f"Migrated history format for {guild_id}_{channel_id}")
                    save_history(guild_id, channel_id, history)
        except json.JSONDecodeError:
            logger.error(f"History file {file_path} is corrupted. Starting fresh.")
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


def get_active_conversations(guild_id, channel_id):
    """
    Get list of users with active conversations in this channel.
    A conversation is active if the user has exchanged messages with the bot
    within the conversation window.

    Returns: dict mapping username -> {last_activity: datetime, message_count: int}
    """
    history = load_history(guild_id, channel_id)
    now = datetime.now()
    window_start = now - timedelta(minutes=CONVERSATION_WINDOW_MINUTES)

    active = {}
    bot_in_conversation = False

    for msg in history:
        if not isinstance(msg, dict):
            continue

        # Parse timestamp
        try:
            ts_str = msg.get("timestamp", "")
            if "." in ts_str:
                ts = datetime.fromisoformat(ts_str)
            else:
                continue  # Skip entries without valid timestamp
        except (ValueError, TypeError):
            continue

        # Skip messages outside the window
        if ts < window_start:
            continue

        role = msg.get("role", "")
        author = msg.get("author")

        if role == "assistant":
            bot_in_conversation = True
        elif role == "user" and author:
            if author not in active:
                active[author] = {"last_activity": ts, "message_count": 0}
            active[author]["last_activity"] = max(active[author]["last_activity"], ts)
            active[author]["message_count"] += 1

    # Only return users if bot was also active (actual conversations)
    if bot_in_conversation:
        # Limit to max users tracked
        if len(active) > CONVERSATION_MAX_USERS_TRACKED:
            # Keep users with most recent activity
            sorted_users = sorted(
                active.items(), key=lambda x: x[1]["last_activity"], reverse=True
            )
            active = dict(sorted_users[:CONVERSATION_MAX_USERS_TRACKED])
        logger.debug(f"Active conversations in {channel_id}: {list(active.keys())}")
        return active

    return {}


def add_to_history(guild_id, channel_id, role, content, author=None):
    """Add a message to history with timestamp and author info.

    Args:
        guild_id: Server ID
        channel_id: Channel ID
        role: 'user' or 'assistant'
        content: Message content
        author: Author name (username for users, None for assistant)
    """
    history = load_history(guild_id, channel_id)
    entry = {
        "role": role,
        "content": content,
        "timestamp": datetime.now().isoformat(),
        "author": author if role == "user" else "bot",
    }
    history.append(entry)
    history = cleanup_history(history)
    save_history(guild_id, channel_id, history)
    return entry


def build_messages_with_system(user_messages):
    """Prepend system prompt to messages if enabled."""
    if not PREPROMPT_ENABLED:
        return user_messages
    return [{"role": "system", "content": PREPROMPT_SYSTEM}] + user_messages


def format_history_context(history, n=10):
    """Format recent history messages for display in prompts."""
    if not history:
        return "No recent conversation history."
    lines = []
    for msg in history[-n:]:
        if isinstance(msg, dict) and "role" in msg and "content" in msg:
            role = "Bot" if msg["role"] == "assistant" else msg.get("author", "User")
            ts = msg.get("timestamp", "")[:19] if msg.get("timestamp") else "???:??:??"
            lines.append(f"[{ts}] {role}: {msg['content'][:120]}")
    return "\n".join(lines) if lines else "No recent conversation history."


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
        """Periodically process queued auto-response messages and generate responses."""
        try:
            async with self.queue_lock:
                if not self.response_queue:
                    return

                # Copy and clear the queue
                current_batch = list(self.response_queue)
                self.response_queue.clear()

            logger.info(
                f"Processing batch of {len(current_batch)} auto-response messages"
            )

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

                # Evaluate the batch to decide if we should respond
                # Load history to provide conversation context
                history = load_history(requests[0]["guild_id"], channel_id)
                should_respond = await evaluate_auto_response_batch(
                    channel.name, requests, history
                )

                if not should_respond:
                    logger.info(f"Batch evaluator decided to SKIP for #{channel.name}")
                    continue

                logger.info(f"Responding to #{channel.name}")

                try:
                    await channel.typing()
                    # Get response using conversation history
                    response = await get_ollama_response(
                        "Please provide a response to the recent conversation in the channel.",
                        requests[0]["guild_id"],
                        requests[0]["channel_id"],
                        include_context=True,
                    )
                    await send_chunked_response(channel, response)
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


async def evaluate_auto_response_batch(channel_name, requests, history):
    """
    Evaluate a batch of auto-response messages to decide if the bot should respond.
    Returns True if should respond, False otherwise.
    Note: This is for non-mention messages only - mentions are handled immediately.

    Args:
        channel_name: Name of the channel
        requests: List of message requests in the batch
        history: Conversation history for context (list of dicts with role/content/timestamp/author)
    """
    # Build the new messages list with timestamps
    messages_list = []
    for req in requests:
        msg = req['message']
        ts = msg.created_at.strftime("%H:%M")
        messages_list.append(f"[{ts}] [{msg.author.display_name}]: {msg.content}")

    batch_content = "\n".join(messages_list)
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Get active conversations
    guild_id = requests[0]["guild_id"]
    channel_id = requests[0]["channel_id"]
    active_conversations = get_active_conversations(guild_id, channel_id)

    # Check which users in the batch have active conversations
    batch_users = set(req["message"].author.display_name for req in requests)
    users_with_active_convos = batch_users & set(active_conversations.keys())

    active_users_str = (
        ", ".join(f"@{u}" for u in users_with_active_convos)
        if users_with_active_convos
        else "none"
    )
    context_str = format_history_context(history, 12)

    eval_prompt = f"""You are a Discord conversation manager. Current time: {current_time}
Evaluate if the bot should respond to messages in channel #{channel_name}.

RECENT CONVERSATION HISTORY (with timestamps):
{context_str}

ACTIVE CONVERSATIONS (users bot talked to recently): {active_users_str}

NEW MESSAGES IN CHANNEL:
{batch_content}

Decision Rules (check in order):
1. MUST RESPOND if any user in the batch has an active conversation with the bot.
2. MUST RESPOND if users are directly addressing or replying to the bot.
3. MUST RESPOND if there are questions directed at the bot or clear need for AI help.
4. RESPOND if there are interesting statements worth commenting on.
5. SKIP only if: spam, gibberish, clearly private conversation between other people, or conversation has naturally ended.

Key insight: If the bot recently responded to a user and they continue talking, continue the conversation.

Respond with exactly one line:
1. "RESPOND" or "SKIP"

Your evaluation:"""

    logger.debug(
        f"Evaluating batch for #{channel_name}, active_users={users_with_active_convos}"
    )
    try:
        test_history = [{"role": "user", "content": eval_prompt}]
        messages_for_api = build_messages_with_system(test_history)

        async with client.session.post(API_URL, json={"model": MODEL, "messages": messages_for_api, "stream": False}) as resp:
            if resp.status == 200:
                data = await resp.json()
                if not isinstance(data, dict) or "message" not in data:
                    logger.error(f"Invalid response structure: {data}")
                    return False
                decision = data["message"].get("content", "").strip().upper()

                if users_with_active_convos and "RESPOND" in decision:
                    logger.warning(
                        f"Batch SKIPped despite active conversations with: {users_with_active_convos}"
                    )
                    logger.debug(
                        f"Full evaluation response: {data['message'].get('content', '')}"
                    )

                should_respond = decision == "RESPOND"
                if not should_respond:
                    logger.debug(f"Batch evaluation: SKIP for #{channel_name}")
                return should_respond
    except Exception as e:
        logger.error(f"Error in evaluate_auto_response_batch: {e}")
        return False

    return False


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
    full_prompt = prompt
    if include_context and history:
        context_parts = format_history_context(history, 10)
        full_prompt = f"Recent conversation context:\n{context_parts}\n\n{prompt}"

    logger.debug(f"Prompt ({'with context' if include_context else 'no context'}): {full_prompt[:100]}...")

    # NOTE: User messages are now added to history in on_message.
    # We only need to add the assistant response here.

    # Build messages with system prompt for API request
    messages_for_api = build_messages_with_system(history)

    try:
        async with client.session.post(API_URL, json={"model": MODEL, "messages": messages_for_api, "stream": False}) as resp:
            if resp.status == 200:
                data = await resp.json()
                # Validate response structure before accessing
                if (
                    not isinstance(data, dict)
                    or "message" not in data
                    or not isinstance(data["message"], dict)
                ):
                    logger.error(f"Invalid Ollama response structure: {data}")
                    return "⚠️ I received an invalid response from the AI server."
                reply = data["message"].get("content")
                if not reply:
                    logger.error(f"Empty or missing content in Ollama response: {data}")
                    return "⚠️ The AI server returned an empty response."

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


async def should_auto_respond(
    message_content, channel_name, guild_id, channel_id, author_name=None
):
    """
    Use the LLM to evaluate if a message is interesting enough to warrant a response.
    Returns (should_respond: bool, reason: str)
    """
    history = load_history(guild_id, channel_id)

    # Get active conversations
    active_conversations = get_active_conversations(guild_id, channel_id)
    has_active_conversation = (
        author_name in active_conversations if author_name else False
    )

    context_str = format_history_context(history, 10)

    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    eval_prompt = f"""You are evaluating if a Discord bot should respond to a message. Current time: {current_time}

RECENT CONVERSATION HISTORY (with timestamps):
{context_str}

NEW MESSAGE from [{author_name or 'Unknown'}] in #{channel_name}:
"{message_content}"

ACTIVE CONVERSATION WITH THIS USER: {'Yes' if has_active_conversation else 'No'}

Decision Rules (check in order):
1. MUST RESPOND if this user has an active conversation with the bot.
2. MUST RESPOND if the user is asking a question or directly addressing the bot.
3. RESPOND to interesting statements, conversation starters, or when you have relevant context.
4. SKIP only if: spam, gibberish, clearly private conversation between other people, or too short/unclear.

Key insight: If the bot and this user were just talking, continue the conversation.

Respond with exactly two lines:
1. "RESPOND" or "SKIP"
2. A brief reason (one sentence)

Your evaluation:"""

    logger.debug(
        f"Evaluating auto-response for [{author_name}] in #{channel_name}, active_conversation={has_active_conversation}"
    )
    try:
        test_history = [{"role": "user", "content": eval_prompt}]
        messages_for_api = build_messages_with_system(test_history)

        async with client.session.post(API_URL, json={"model": MODEL, "messages": messages_for_api, "stream": False, "options": {"temperature": TEMPERATURE, "top_p": TOP_P, "num_predict": NUM_PREDICT}}) as resp:
            if resp.status == 200:
                data = await resp.json()
                if not isinstance(data, dict) or "message" not in data:
                    logger.error(
                        f"Invalid response structure in should_auto_respond: {data}"
                    )
                    return False, "Invalid response from AI server"
                evaluation = data["message"].get("content", "")
                lines = evaluation.strip().split('\n')
                should_respond = lines[0].upper() == "RESPOND" if lines else False
                reason = lines[1] if len(lines) > 1 else "No reason given"

                if has_active_conversation and not should_respond:
                    logger.warning(
                        f"should_auto_respond SKIPped despite active conversation with [{author_name}]"
                    )

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
        f"**Auto-response:** {'Enabled' if CHANNEL_RESPONSE_ENABLED else 'Disabled'}\n"
        f"**Conversation window:** `{CONVERSATION_WINDOW_MINUTES} minutes`"
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
    add_to_history(
        guild_id,
        channel_id,
        "user",
        message.content,
        author=message.author.display_name,
    )

    # Check if bot is mentioned (takes priority)
    is_mentioned = client.user.mentioned_in(message)

    if is_mentioned:
        logger.info(f"[MENTION] {message.author.display_name} mentioned bot in #{message.channel.name}")
        # Handle direct mention - respond immediately
        try:
            await message.channel.typing()
            response = await get_ollama_response(
                message.content, guild_id, channel_id, include_context=True
            )
            await send_chunked_response(message.channel, response, reply_to=message)
            logger.info(f"Response sent to mention in #{message.channel.name}")
        except Exception as e:
            logger.error(f"Error handling mention: {e}")
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
                client.response_queue.append(
                    {
                        "message": message,
                        "timestamp": message.created_at.timestamp(),
                        "channel_id": channel_id,
                        "guild_id": guild_id,
                    }
                )
        else:
            logger.debug(f"[AUTO-RESPOND] Skipping message in #{message.channel.name}: {reason}")

client.run(TOKEN)
