import asyncio
import json
import logging
import os
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
import discord
import requests
import yaml
from discord import app_commands
from dotenv import load_dotenv
import contextlib
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client
from engine import registry, triggers
from engine.timer_engine import get_engine
load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

# MCP tool definitions (populated at startup)
mcp_tools = []

# Active MCP client sessions and transports, keyed by server name
mcp_sessions: dict[str, ClientSession] = {}
mcp_transports: dict[str, contextlib.AsyncExitStack] = {}

# Local timer tools (non-MCP)
# (defined after functions below)

# Context for the current message being processed
_current_author_id: int = 0

async def _execute_add_reminder(args):
    engine = get_engine()
    channel_id = args.get("channel_id", 0)
    user_str = args["user"]
    message_author_id = args.get("message_author_id", 0)
    
    if user_str.startswith("<@") or user_str.startswith("<@!"):
        user_id = int(user_str.strip("<>@!>"))
    elif user_str.isdigit():
        user_id = int(user_str)
    else:
        user_id = message_author_id
    
    # If user_id is still 0 (Llama didn't parse the mention or sent user_id=0), fall back to message_author_id
    if not user_id and message_author_id:
        user_id = message_author_id
    elif not user_id:
        user_id = channel_id
    
    result = await engine.add_reminder(channel_id, user_str, args["message"], args["delay_minutes"], user_id)
    return f"Reminder set: {result['message']} in {result['time_until']} (ID: {result['id']})"

async def _execute_list_reminders(args):
    engine = get_engine()
    reminders = await engine.list_reminders(args.get("channel_id"))
    if not reminders:
        return "No pending reminders."
    lines = ["## Pending Reminders"]
    for r in reminders:
        lines.append(f"- {r['id']}: {r['message']} (in {r['time_until']}) — by {r['user']}")
    return "\n".join(lines)

async def _fire_reminder_callback(reminder):
    logger.info(f"Reminder fired callback triggered: id={reminder.id}, channel_id={reminder.channel_id}, user={reminder.user}, user_id={reminder.user_id}, message={reminder.message!r}")
    if reminder.channel_id:
        channel = client.get_channel(reminder.channel_id)
        if channel:
            logger.info(f"Sending reminder to channel {reminder.channel_id}: {reminder.message!r}")
            await channel.send(f"Reminder: {reminder.user} — {reminder.message}")
        else:
            logger.error(f"Channel {reminder.channel_id} not found")
    else:
        logger.info(f"Sending DM reminder to user_id {reminder.user_id}: {reminder.message!r}")
        try:
            user = await client.fetch_user(reminder.user_id)
            logger.info(f"Fetched user: {user.name} (ID: {user.id})")
            await user.send(f"Reminder: {reminder.message}")
            logger.info(f"DM sent successfully to {user.name}")
        except Exception as e:
            logger.error(f"Failed to DM reminder to {reminder.user} (user_id={reminder.user_id}): {e}")


async def _execute_cancel_reminder(args):
    engine = get_engine()
    try:
        r = await engine.cancel_reminder(args["reminder_id"])
        return f"Cancelled: {r['message']} (ID: {r['id']})"
    except ValueError as e:
        return str(e)

local_timer_tools = [
    {
        "type": "function",
        "function": {
            "name": "add_reminder",
            "description": "Start a timer/reminder. channel_id (int), user (@mention), message (str), delay_minutes (float).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "channel_id": {"type": "integer", "description": "Discord channel ID where reminder will fire (use 0 for personal/DM reminder)"},
                        "user": {"type": "string", "description": "User mention string (e.g. @username)"},
                        "user_id": {"type": "integer", "description": "Discord user ID of the person to remind (used for DM when channel_id is 0)"},
                        "message": {"type": "string", "description": "What to remind about, e.g. 'drink water'"},
                        "delay_minutes": {"type": "number", "description": "Minutes from now until the reminder fires"},
                    },
                    "required": ["user", "message", "delay_minutes"],
                },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_reminders",
            "description": "List all pending reminders. Optional channel_id to filter.",
            "parameters": {
                "type": "object",
                "properties": {
                    "channel_id": {"type": "integer", "description": "Optional: filter to specific channel"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_reminder",
            "description": "Cancel a pending reminder by its ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reminder_id": {"type": "string", "description": "The reminder ID to cancel"},
                },
                "required": ["reminder_id"],
            },
        },
    },
]

_timer_tools = {
    "add_reminder": _execute_add_reminder,
    "list_reminders": _execute_list_reminders,
    "cancel_reminder": _execute_cancel_reminder,
}

def load_config():
    with open("config.yaml", "r") as f:
        return yaml.safe_load(f)


def save_config():
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
        logger.debug(f"Cleaned up history for {guild_id}_{channel_id}")


def update_skills(channel_id: int, content: str, is_mention: bool) -> bool:
    new_skills = []
    if is_mention:
        active_skills[channel_id] = []
        return True

    matches = triggers.match_skill(content, skills_registry, skill_threshold)
    for match in matches:
        skill_name = match["skill_name"]
        skill = skills_registry.get_skill(skill_name)
        if skill:
            new_skills.append({"name": skill_name, "body": skill.body})

    active_skills[channel_id] = new_skills
    return bool(matches)


def build_skill_injection(skills: list[dict]) -> str | None:
    if not skills:
        return None
    parts = []
    for skill in skills:
        parts.append(f"\n### {skill['name'].upper()} SKILL:\n{skill['body']}")
    return "\n".join(parts)


def create_skill_command(skill_name: str):
    async def cmd(interaction: discord.Interaction):
        skill = skills_registry.get_skill(skill_name)
        if skill:
            active_skills[interaction.channel_id] = [{"name": skill_name, "body": skill.body}]
            await interaction.response.send_message(f"Skill `{skill_name}` activated. What would you like to do?", ephemeral=True)
        else:
            await interaction.response.send_message(f"Skill '{skill_name}' not found", ephemeral=True)
    return cmd


def build_prompt(channel_id: int, messages: list[dict], author_id: int) -> list[dict]:
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


async def setup_mcp():
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

    # Merge local timer tools
    mcp_tools = local_timer_tools + mcp_tools
    logger.info(f"Total tools available: {len(mcp_tools)} (including {len(local_timer_tools)} local timer tools)")

    # Start timer engine
    engine = get_engine()
    await engine.load()
    engine.set_fire_callback(_fire_reminder_callback)
    engine.start_monitor()


async def execute_mcp_tool(tool_name: str, arguments: dict, author_id: int = 0) -> str:
    # Check local timer tools first before MCP servers
    for timer_tool in local_timer_tools:
                if timer_tool["function"]["name"] == tool_name:
                    arguments["message_author_id"] = author_id
                    return await _timer_tools[tool_name](arguments)

    # Find which server owns this tool by checking all sessions
    for server_name, session in mcp_sessions.items():
        try:
            tools_result = await session.list_tools()
            for tool in tools_result.tools:
                if tool.name == tool_name:
                    result = await session.call_tool(tool_name, arguments)
                    # Convert result to string
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

async def call_ollama(messages: list[dict]) -> str:
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
                tool_result = await execute_mcp_tool(name, args, _current_author_id)
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
    guild_id: int, channel_id: int, content: str, author: str, is_mention: bool, author_id: int = 0
) -> None:
    key = f"{guild_id}_{channel_id}"

    update_skills(channel_id, content, is_mention)

    if not is_mention:
        matched = any(m["confidence"] > 0.5 for m in triggers.match_skill(content, skills_registry, skill_threshold))
        if matched:
            logger.info(f"Active skills for {channel_id}: {[s['name'] for s in active_skills.get(channel_id, [])]}")
    channel = client.get_channel(channel_id)
    if not channel:
        logger.warning(f"Channel {channel_id} not found")
        return

    cleanup_history(guild_id, channel_id)

    history[key].append(
        {
            "role": "user",
            "content": content,
            "author": author,
            "author_id": author_id,
            "timestamp": datetime.now().isoformat(),
        }
    )

    prompt = build_prompt(channel_id, history[key], author_id)
    logger.debug(f"Prompt: {prompt}")

    try:
        global _current_author_id
        _current_author_id = author_id
        async with channel.typing():
            response = await call_ollama(prompt)
            logger.info(f"Response: {response}")

        history[key].append(
            {
                "role": "assistant",
                "content": response,
                "author": "Mech Knight",
                "timestamp": datetime.now().isoformat(),
            }
        )
        save_history(guild_id, channel_id)

        await channel.send(response)
    except Exception as e:
        logger.error(f"Error processing message: {e}")


async def evaluate_should_respond(
    guild_id: int, channel_id: int, content: str, author: str
) -> bool:
    if len(content) < config["channel"]["min_message_length"]:
        return False

    key = f"{guild_id}_{channel_id}"
    recent_users = active_users.get(channel_id, {})
    if author in recent_users:
        last_seen = recent_users[author]
        if datetime.now() - last_seen < timedelta(
            minutes=config["conversation"]["window_minutes"]
        ):
            logger.debug(f"Active user {author} in channel {channel_id}, responding")
            return True

    eval_prompt = [
        {"role": "system", "content": config["preprompt"]["system"]},
        {
            "role": "system",
            "content": f"Current date/time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        },
        {
            "role": "user",
            "content": f"""Evaluate if the bot should respond to this message:

"{content}"

Respond with ONLY "YES" or "NO.""",
        },
    ]

    try:
        result = await call_ollama(eval_prompt)
        should_respond = result.strip().upper().startswith("YES")
        logger.debug(f"Auto-response evaluation: {should_respond}")
        return should_respond
    except Exception as e:
        logger.error(f"Error evaluating response: {e}")
        return False


@client.event
async def on_ready():
    logger.info(f"Logged in as {client.user} (ID: {client.user.id})")
    skills_registry.load_all()
    for cmd_name in skills_registry.get_all_names():
        skill = skills_registry.get_skill(cmd_name) if skills_registry.get_skill(cmd_name) else None
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

    if not content:
        return

    guild_id = message.guild.id if message.guild else 0
    key = f"{guild_id}_{message.channel.id}"
    active_users[message.channel.id][message.author.name] = datetime.now()

    if is_mention:
        await process_message(guild_id, message.channel.id, content, message.author.name, is_mention, message.author.id)
    elif config["channel"]["enabled"]:
        if await evaluate_should_respond(guild_id, message.channel.id, content, message.author.name):
            await process_message(guild_id, message.channel.id, content, message.author.name, is_mention, message.author.id)
        else:
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
    skill_list = []
    for name, skill in skills_registry.get_all_descriptions().items():
        if len(skill) > 1000:
            skill = skill[:997] + "..."
        skill_list.append(f"- `{name}`: {skill}")
    embed = discord.Embed(title="Available Skills", color=discord.Color.blue())
    for i in range(0, len(skill_list), 10):
        chunk = "\n".join(skill_list[i:i+10])
        embed.add_field(name="", value=chunk, inline=False)
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


async def main():
    await setup_mcp()
    asyncio.create_task(_start_reminder_monitor())
    await client.start(DISCORD_TOKEN)


async def _start_reminder_monitor():
    await asyncio.sleep(1)
    engine = get_engine()
    await engine.load()
    engine.set_fire_callback(_fire_reminder_callback)
    engine.start_monitor()


if __name__ == "__main__":
    asyncio.run(main())
