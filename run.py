"""Discord AI chatbot with Ollama LLM integration."""

import asyncio
import json
import logging
import os
import signal
import traceback
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
import discord
import requests
import yaml
from discord import app_commands
from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client
import contextlib
from engine import registry, triggers
from engine.message_queue import Message, MessageQueue
from engine.timer_engine import get_engine as get_timer_engine, get_tools as timer_get_tools, execute_timer_tool
from engine.memory_engine import get_engine as get_memory_engine, get_tools as memory_get_tools, execute_tool as execute_memory_tool
load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

# MCP tool definitions (populated at startup)
mcp_tools: list[dict] = []

# Active MCP client sessions and transports, keyed by server name
mcp_sessions: dict[str, ClientSession] = {}
mcp_transports: dict[str, contextlib.AsyncExitStack] = {}

message_queue = MessageQueue()

async def _fire_reminder_callback(reminder):
    if reminder.channel_id:
        channel = client.get_channel(reminder.channel_id)
        if channel:
            logger.info(f"Sending reminder to channel {reminder.channel_id}: {reminder.message!r}")
            await channel.send(f"Reminder: {reminder.user} — {reminder.message}")
        else:
            logger.error(f"Channel {reminder.channel_id} not found")
    else:
        try:
            user = await client.fetch_user(reminder.user_id)
            await user.send(f"Reminder: {reminder.message}")
            logger.info(f"DM reminder sent to {user.name}")
        except Exception as e:
            logger.error(f"Failed to DM reminder to {reminder.user} (user_id={reminder.user_id}): {e}")

def load_config() -> dict:
    """Load configuration from config.yaml."""
    with open("config.yaml", "r") as f:
        return yaml.safe_load(f)


def save_config() -> None:
    """Save current config dict to config.yaml."""
    with open("config.yaml", "w") as f:
        yaml.dump(config, f)


config = load_config()

skills_registry = registry.get_registry()
skill_threshold = config.get("skills", {}).get("trigger_threshold", 0.3)
active_skills: dict[int, list[dict]] = defaultdict(list)

logging.basicConfig(
    level=getattr(logging, config["logging"]["level"].upper()),
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    handlers=[
        logging.FileHandler(config["logging"]["file"]),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

history: dict[str, list[dict]] = defaultdict(list)
active_users: dict[int, dict[str, datetime]] = defaultdict(dict)
history: dict[str, list[dict]] = defaultdict(list)

HISTORY_DIR = Path("data/history")
HISTORY_DIR.mkdir(parents=True, exist_ok=True)


def load_history(guild_id: int, channel_id: int) -> list[dict]:
    path = HISTORY_DIR / f"{guild_id}_{channel_id}.json"
    if path.exists():
        with open(path, "r") as f:
            return json.load(f)
    return []


def save_history(guild_id: int, channel_id: int) -> None:
    path = HISTORY_DIR / f"{guild_id}_{channel_id}.json"
    key = f"{guild_id}_{channel_id}"
    with open(path, "w") as f:
        json.dump(history[key], f, indent=2)


def cleanup_history(guild_id: int, channel_id: int) -> None:
    """Remove expired and excess messages from channel history."""
    key = f"{guild_id}_{channel_id}"
    max_msgs = config["history"]["max_messages"]
    window = config["conversation"]["window_minutes"]
    cutoff = datetime.now() - timedelta(minutes=window)

    msgs = history[key]
    history[key] = [m for m in msgs if datetime.fromisoformat(m["timestamp"]) > cutoff]

    if len(history[key]) > max_msgs:
        history[key] = history[key][-max_msgs:]

    if history[key] != msgs:
        save_history(guild_id, channel_id)
        logger.debug("Cleaned up history for %s", key)


def update_skills(channel_id: int, content: str, is_mention: bool) -> bool:
    """Update active skills for a channel. Resets skills on mention."""
    if is_mention:
        active_skills[channel_id] = []
        return True

    matches = triggers.match_skill(content, skills_registry, skill_threshold)
    new_skills = []
    for match in matches:
        skill_name = match["skill_name"]
        skill = skills_registry.get_skill(skill_name)
        if skill:
            new_skills.append({"name": skill_name, "body": skill.body})

    changed = active_skills[channel_id] != new_skills
    active_skills[channel_id] = new_skills
    if changed and new_skills:
        logger.info("Skills updated for channel %d: %s", channel_id, [s["name"] for s in new_skills])
    return bool(matches)


def build_skill_injection(skills: list[dict]) -> str | None:
    """Format active skills as a string for injection into the prompt."""
    if not skills:
        return None
    parts = []
    for skill in skills:
        parts.append(f"\n### {skill['name'].upper()} SKILL:\n{skill['body']}")
    return "\n".join(parts)


def create_skill_command(skill_name: str):
    """Create a slash command callback for activating a skill."""
    async def cmd(interaction: discord.Interaction):
        skill = skills_registry.get_skill(skill_name)
        if skill:
            active_skills[interaction.channel_id] = [{"name": skill_name, "body": skill.body}]
            await interaction.response.send_message(
                f"Skill `{skill_name}` activated. What would you like to do?",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                f"Skill '{skill_name}' not found", ephemeral=True
            )
    return cmd


def build_prompt(channel_id: int, messages: list[dict], author_id: int) -> list[dict]:
    """Build the full prompt with system messages, skills, and chat history."""
    msgs = []
    if config["preprompt"]["enabled"]:
        msgs.append({"role": "system", "content": config["preprompt"]["system"]})

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    msgs.append({"role": "system", "content": f"Current date/time: {now}"})

    skill_content = build_skill_injection(active_skills.get(channel_id, []))
    if skill_content:
        msgs.append({"role": "system", "content": f"You have active skills:\n{skill_content}"})

    if mcp_tools:
        tool_names = ", ".join(t["function"]["name"] for t in mcp_tools)
        msgs.append(
            {
                "role": "system",
                "content": f"You have access to web tools: {tool_names}. Use them when you need current information from the internet.",
            }
        )
    
    if author_id:
        msgs.append({"role": "system", "content": f"Current message author ID: {author_id}. You can use this in user_id fields."})

    for m in messages:
        msgs.append({"role": m["role"], "content": m["content"]})

    return msgs


async def setup_mcp() -> None:
    """Initialize MCP servers and timer engine tools."""
    global mcp_tools
    mcp_config = config.get("mcp", {})
    if not mcp_config.get("enabled", False):
        logger.info("MCP is disabled in config")
        return

    servers = mcp_config.get("servers", [])
    if not servers:
        logger.info("No MCP servers configured")
        return

    all_tools = []

    for server in servers:
        name = server["name"]
        command = server["command"]
        args = server.get("args", [])
        logger.info(f"Starting MCP server: {name}")

        server_params = StdioServerParameters(
            command=command,
            args=args,
        )

        try:
            # Create an exit stack to manage the MCP transport lifecycle
            stack = contextlib.AsyncExitStack()
            mcp_transports[name] = stack

            # Keep stdio transport alive for the lifetime of the bot
            transport = await stack.enter_async_context(stdio_client(server_params))
            read, write = transport

            session = ClientSession(read, write)
            await stack.enter_async_context(session)
            await session.initialize()

            mcp_sessions[name] = session

            tools_result = await session.list_tools()
            server_tools = []
            for tool in tools_result.tools:
                mcp_tools_entry = {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description or "",
                        "parameters": {
                            "type": "object",
                            "properties": tool.inputSchema.get("properties", {})
                            if tool.inputSchema else {},
                            "required": tool.inputSchema.get("required", [])
                            if tool.inputSchema else [],
                        },
                    },
                }
                server_tools.append(mcp_tools_entry)
                all_tools.append(mcp_tools_entry)
                logger.info(f"  Tool: {tool.name} - {tool.description}")

            mcp_tools = all_tools
            logger.info(f"MCP server '{name}' ready with {len(server_tools)} tools")
        except Exception as e:
            logger.error(f"Failed to start MCP server '{name}': {e}")

    # Add timer tools
    timer_tools = timer_get_tools()
    mcp_tools = timer_tools + mcp_tools
    logger.info(f"Total tools available: {len(mcp_tools)} (including {len(timer_tools)} timer tools)")

    # Start timer engine
    timer_engine = get_timer_engine()
    await timer_engine.load()
    timer_engine.set_fire_callback(_fire_reminder_callback)
    timer_engine.start_monitor()

    # Add memory tools
    memory_tools = memory_get_tools()
    mcp_tools = memory_tools + mcp_tools
    logger.info(f"Total tools available: {len(mcp_tools)} (including {len(memory_tools)} memory tools)")

    # Start memory engine
    memory_engine = get_memory_engine()
    await memory_engine.load()


async def execute_mcp_tool(tool_name: str, arguments: dict, author_id: int = 0) -> str:
    """Execute an MCP tool call, dispatching to the correct server or timer engine."""
    # Dispatch to timer engine first (handles add/list/cancel_reminder)
    if tool_name in ("add_reminder", "list_reminders", "cancel_reminder"):
        # Ensure channel_id has a default so the LLM doesn't need to provide it
        arguments.setdefault("channel_id", 0)
        arguments["message_author_id"] = author_id
        return await execute_timer_tool(tool_name, arguments)

    # Dispatch to memory engine
    if tool_name in ("remember", "recall", "forget_memory"):
        arguments.setdefault("channel_id", 0)
        return await execute_memory_tool(tool_name, arguments, author_id)

    # Find which MCP server owns this tool
    for server_name, session in mcp_sessions.items():
        try:
            tools_result = await session.list_tools()
            for tool in tools_result.tools:
                if tool.name == tool_name:
                    result = await session.call_tool(tool_name, arguments)
                    parts = []
                    for content in result.content:
                        if isinstance(content, types.TextContent):
                            parts.append(content.text)
                        elif hasattr(content, "text"):
                            parts.append(str(content.text))
                        else:
                            parts.append(str(content))
                    return "\n".join(parts) if parts else str(result)
        except Exception as e:
            logger.error(f"Error calling tool '{tool_name}' on server '{server_name}': {e}")
            return f"Error calling tool '{tool_name}': {e}"

    return f"Error: tool '{tool_name}' not found on any MCP server"

async def call_ollama(messages: list[dict], author_id: int = 0) -> str:
    """Call the Ollama API with tool calling support, handling multi-turn tool use."""
    url = config["ollama"]["api_url"]
    payload = {
        "model": config["ollama"]["model"],
        "messages": messages,
        "stream": False,
        "tools": mcp_tools if mcp_tools else None,
    }
    if payload["tools"] is None:
        del payload["tools"]

    logger.debug(
        f"Calling Ollama: model={payload['model']}, tools={len(mcp_tools) if mcp_tools else 0}"
    )

    max_tool_calls = 5
    for _ in range(max_tool_calls):
        resp = requests.post(url, json=payload, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        message = data["message"]
        logger.debug(f"Ollama response: {message}")

        # Check if model wants to call a tool
        if "tool_calls" in message and message["tool_calls"]:
            for tool_call in message["tool_calls"]:
                func = tool_call.get("function", {})
                name = func.get("name", "")
                args_str = func.get("arguments", "{}")
                if args_str is None:
                    args_str = "{}"
                if isinstance(args_str, str):
                    args = json.loads(args_str)
                else:
                    args = args_str

                logger.info(f"Tool call: {name}({json.dumps(args)})")
                tool_result = await execute_mcp_tool(name, args, author_id)
                logger.debug(f"Tool result: {tool_result[:200]}")

                # Add tool call + result to messages
                messages.append(
                    {"role": "assistant", "content": None, "tool_calls": [tool_call]}
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.get("id", "0"),
                        "content": tool_result,
                    }
                )

                # Update payload for next iteration
                payload["messages"] = messages
        else:
            return message.get("content", "")

    return "Error: exceeded maximum tool calls"


async def process_message(
    msg: Message,
) -> None:
    """Process a message: update skills, append to history, call LLM, and reply.

    All shared state mutations happen here — called only from the processor
    loop which runs single-threaded, so no locking is needed.
    """
    key = f"{msg.guild_id}_{msg.channel_id}"

    update_skills(msg.channel_id, msg.content, msg.is_mention)
    current_skills = [s["name"] for s in active_skills.get(msg.channel_id, [])]
    logger.info(
        "Processing message from %s — active skills: %s",
        msg.author,
        current_skills if current_skills else "none",
    )

    channel = client.get_channel(msg.channel_id)
    if not channel:
        logger.warning("Channel %d not found", msg.channel_id)
        return

    cleanup_history(msg.guild_id, msg.channel_id)

    history[key].append(
        {
            "role": "user",
            "content": msg.content,
            "author": msg.author,
            "author_id": msg.author_id,
            "timestamp": msg.timestamp.isoformat(),
        }
    )

    prompt = build_prompt(msg.channel_id, history[key], msg.author_id)
    logger.debug("Prompt: %s", prompt)

    try:
        logger.info("Calling LLM for message from %s", msg.author)
        async with channel.typing():
            response = await call_ollama(prompt, msg.author_id)
        logger.info("LLM response for %s: %s", msg.author, response[:120])

        history[key].append(
            {
                "role": "assistant",
                "content": response,
                "author": "Mech Knight",
                "timestamp": datetime.now().isoformat(),
            }
        )
        save_history(msg.guild_id, msg.channel_id)

        logger.info("Sending reply to %s in channel %d", msg.author, msg.channel_id)
        await channel.send(response)
    except Exception as e:
        logger.error("Error processing message: %s", e)


async def evaluate_should_respond(
    msg: Message,
) -> bool:
    """Decide whether the bot should auto-respond to a channel message.

    Includes recent conversation history so the LLM can evaluate context.
    Active-user shortcut fires only if the user has recently interacted
    with the bot (sent a message within the conversation window).
    """
    if len(msg.content) < config["channel"]["min_message_length"]:
        logger.info(
            "Eval %s: message too short (%d < %d), will not respond",
            msg.author,
            len(msg.content),
            config["channel"]["min_message_length"],
        )
        return False

    key = f"{msg.guild_id}_{msg.channel_id}"

    # Include recent history for context — last 6 messages max
    recent_history = history.get(key, [])[-6:]
    history_excerpt = "\n".join(
        f'{m.get("author", "unknown")}: {m["content"]}'
        for m in recent_history
    )

    eval_prompt = [
        {"role": "system", "content": config["preprompt"]["system"]},
        {
            "role": "system",
            "content": f"Current date/time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        },
        {
            "role": "system",
            "content": (
                f"Recent conversation context:\n{history_excerpt}\n\n"
                f"New message from {msg.author}: \"{msg.content}\"\n\n"
                f"Should the bot respond to this new message? "
                f'Respond with ONLY "YES" or "NO".'
            ),
        },
    ]

    try:
        logger.info("Eval %s: asking LLM whether to respond", msg.author)
        result = await call_ollama(eval_prompt, 0)
        should_respond = result.strip().upper().startswith("YES")
        logger.info(
            "Eval %s: LLM decision = %s, will respond = %s",
            msg.author,
            result.strip()[:80],
            should_respond,
        )
        return should_respond
    except Exception as e:
        logger.error("Error evaluating response: %s", e)
        return False


@client.event
async def on_ready():
    logger.info(f"Logged in as {client.user} (ID: {client.user.id})")
    skills_registry.load_all()
    for cmd_name in skills_registry.get_all_names():
        skill = skills_registry.get_skill(cmd_name)
        desc = skill.description if skill and len(skill.description) <= 100 else "Use this skill for related tasks."
        cmd = app_commands.Command(name=cmd_name, description=desc, callback=create_skill_command(cmd_name))
        tree.add_command(cmd)
        logger.info(f"Registered skill command: /{cmd_name}")
    synced = await tree.sync()
    logger.info(f"Synced {len(synced)} commands globally")
    for cmd in synced:
        logger.info(f"  - {cmd.name}")


@client.event
async def on_disconnect():
    # Clean up MCP server transports
    for name, stack in list(mcp_transports.items()):
        try:
            await stack.aclose()
            logger.info(f"Closed MCP transport for '{name}'")
        except Exception as e:
            logger.error(f"Error closing MCP transport for '{name}': {e}")
    mcp_transports.clear()
    mcp_sessions.clear()


@client.event
async def on_message(message):
    if message.author == client.user:
        return

    if client.user is None:
        return

    is_mention = client.user.mentioned_in(message)

    if is_mention:
        content = (
            message.content.replace(f"<@{client.user.id}>", "")
            .replace(f"<@!{client.user.id}>", "")
            .strip()
        )
    else:
        content = message.content

    if not content:
        return

    guild_id = message.guild.id if message.guild else 0
    active_users[message.channel.id][message.author.name] = datetime.now()

    if is_mention:
        logger.info(
            "Received mention from %s in channel %d: %r",
            message.author.name,
            message.channel.id,
            content,
        )
        await message_queue.enqueue(Message(
            guild_id=guild_id,
            channel_id=message.channel.id,
            content=content,
            author=message.author.name,
            author_id=message.author.id,
            is_mention=True,
        ))
    elif config["channel"]["enabled"]:
        logger.info(
            "Received channel message from %s in channel %d: %r",
            message.author.name,
            message.channel.id,
            content,
        )
        await message_queue.enqueue(Message(
            guild_id=guild_id,
            channel_id=message.channel.id,
            content=content,
            author=message.author.name,
            author_id=message.author.id,
            is_mention=False,
        ))
    else:
        key = f"{guild_id}_{message.channel.id}"
        history[key].append(
            {
                "role": "user",
                "content": content,
                "author": message.author.name,
                "author_id": message.author.id,
                "timestamp": datetime.now().isoformat(),
            }
        )
        save_history(guild_id, message.channel.id)


@tree.command(name="status", description="Show bot status")
async def status_command(interaction: discord.Interaction):
    embed = discord.Embed(title="Bot Status", color=discord.Color.blue())
    embed.add_field(name="Model", value=config["ollama"]["model"], inline=True)
    embed.add_field(
        name="Auto-response",
        value="Enabled" if config["channel"]["enabled"] else "Disabled",
        inline=True,
    )
    embed.add_field(
        name="Window",
        value=f"{config['conversation']['window_minutes']} minutes",
        inline=True,
    )
    await interaction.response.send_message(embed=embed)


@tree.command(
    name="clear_history", description="Clear conversation history for current channel"
)
async def clear_history_command(interaction: discord.Interaction):
    channel_id = interaction.channel_id
    guild_id = interaction.guild_id if interaction.guild else 0
    key = f"{guild_id}_{channel_id}"

    history[key] = []
    path = HISTORY_DIR / f"{guild_id}_{channel_id}.json"
    if path.exists():
        path.unlink()

    logger.info(f"Cleared history for {key}")
    await interaction.response.send_message(
        "Conversation history cleared!", ephemeral=True
    )


@tree.command(
    name="set_min_length", description="Set minimum message length for auto-response"
)
@app_commands.describe(length="Minimum message length in characters")
async def set_min_length_command(interaction: discord.Interaction, length: int):
    config["channel"]["min_message_length"] = length
    save_config()

    logger.info(f"Set min_message_length to {length}")
    await interaction.response.send_message(
        f"Minimum message length set to {length} characters!", ephemeral=True
    )


@tree.command(name="skills", description="List all available skills")
async def skills_command(interaction: discord.Interaction):
    embed = discord.Embed(title="Available Skills", color=discord.Color.blue())
    prefix = "- "
    for name, skill in skills_registry.get_all_descriptions().items():
        line = f"{prefix}`{name}`: {skill}"
        if len(line) > 1000:
            line = f"{prefix}`{name}`: {skill[:1000 - len(prefix) - len(name) - 2]}.."
        embed.add_field(name="", value=line, inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@tree.command(name="show_preprompt", description="Show the current preprompt")
async def show_preprompt_command(interaction: discord.Interaction):
    if config["preprompt"]["enabled"]:
        preprompt = config["preprompt"]["system"]
        # Discord embeds have a 1024 char limit per field, so chunk if needed
        if len(preprompt) <= 1024:
            embed = discord.Embed(
                title="Current Preprompt",
                description=preprompt,
                color=discord.Color.blue(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            # Split into multiple embeds
            chunks = [preprompt[i : i + 1024] for i in range(0, len(preprompt), 1024)]
            for i, chunk in enumerate(chunks):
                embed = discord.Embed(
                    title=f"Preprompt (Part {i+1}/{len(chunks)})",
                    description=chunk,
                    color=discord.Color.blue(),
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
    else:
        await interaction.response.send_message(
            "Preprompt is currently disabled.", ephemeral=True
        )


@tree.command(name="set_preprompt", description="Set the bot's preprompt")
@app_commands.describe(preprompt="The new preprompt text")
async def set_preprompt_command(interaction: discord.Interaction, preprompt: str):
    config["preprompt"]["enabled"] = True
    config["preprompt"]["system"] = preprompt
    save_config()

    logger.info("Updated preprompt")
    await interaction.response.send_message(
        "Preprompt updated successfully!", ephemeral=True
    )


async def _processor_loop():
    """Background loop that drains the message queue and processes messages.

    Runs single-threaded in the asyncio event loop, so access to shared state
    (history, active_skills, active_users) is naturally serialized.
    """
    logger.info("Processor loop started")
    while True:
        try:
            msg = await message_queue.dequeue(asyncio.get_event_loop().time() + 0.5)
            logger.info(
                "Dequeued message from %s in channel %d (mention=%s)",
                msg.author,
                msg.channel_id,
                msg.is_mention,
            )
            active_users[msg.channel_id][msg.author] = datetime.now()

            if msg.is_mention:
                await process_message(msg)
            else:
                if await evaluate_should_respond(msg):
                    await process_message(msg)
                else:
                    logger.info(
                        "Eval %s: decided NOT to respond, storing in history only",
                        msg.author,
                    )
                    key = f"{msg.guild_id}_{msg.channel_id}"
                    history[key].append(
                        {
                            "role": "user",
                            "content": msg.content,
                            "author": msg.author,
                            "author_id": msg.author_id,
                            "timestamp": msg.timestamp.isoformat(),
                        }
                    )
                    save_history(msg.guild_id, msg.channel_id)
        except asyncio.TimeoutError:
            continue
        except Exception:
            logger.error("Processor loop error:\n%s", traceback.format_exc())


async def main():
    """Start the bot: initialize MCP, start reminder monitor, start processor, connect to Discord."""
    await setup_mcp()
    asyncio.create_task(_start_reminder_monitor())
    asyncio.create_task(_processor_loop())
    await client.start(DISCORD_TOKEN)


async def _start_reminder_monitor():
    """Start the timer engine's reminder monitor after a short delay."""
    await asyncio.sleep(1)
    engine = get_timer_engine()
    await engine.load()
    engine.set_fire_callback(_fire_reminder_callback)
    engine.start_monitor()


def _on_shutdown(loop: asyncio.AbstractEventLoop):
    """Signal handler callback to gracefully shut down the bot."""
    logger.info("Shutdown signal received, stopping bot...")
    loop.call_soon_threadsafe(loop.stop)


if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _on_shutdown, loop)

    try:
        loop.run_until_complete(main())
    except BaseException as e:
        # CancelledError (from Ctrl-C) is BaseException, not Exception
        if not isinstance(e, asyncio.CancelledError | KeyboardInterrupt):
            logger.exception("Unhandled error in main loop")
    finally:
        # Cancel timer monitor so the event loop can close
        engine = get_timer_engine()
        engine.stop_monitor()
        loop.run_until_complete(client.close())
        loop.close()
        logger.info("Bot shut down complete")
