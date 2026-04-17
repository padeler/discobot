import asyncio
import json
import logging
import os
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from queue import Queue

import discord
import requests
import yaml
from discord import app_commands
from dotenv import load_dotenv

load_dotenv()

with open("config.yaml", "r") as f:
    config = yaml.safe_load(f)

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

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

message_queue: Queue = Queue()
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


def call_ollama(messages: list[dict]) -> str:
    url = config["ollama"]["api_url"]
    payload = {
        "model": config["ollama"]["model"],
        "messages": messages,
        "stream": False,
    }
    logger.debug(f"Calling Ollama: {payload}")
    resp = requests.post(url, json=payload, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    return data["message"]["content"]


def build_prompt(messages: list[dict]) -> list[dict]:
    msgs = []
    if config["preprompt"]["enabled"]:
        msgs.append({"role": "system", "content": config["preprompt"]["system"]})

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    msgs.append({"role": "system", "content": f"Current date/time: {now}"})

    for m in messages:
        msgs.append({"role": m["role"], "content": m["content"]})

    return msgs


async def process_message(
    guild_id: int, channel_id: int, content: str, author: str
) -> None:
    key = f"{guild_id}_{channel_id}"
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
            "timestamp": datetime.now().isoformat(),
        }
    )

    prompt = build_prompt(history[key])
    logger.debug(f"Prompt: {prompt}")

    try:
        async with channel.typing():
            response = await asyncio.to_thread(call_ollama, prompt)
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

Respond with ONLY "YES" or "NO".""",
        },
    ]

    try:
        result = await asyncio.to_thread(call_ollama, eval_prompt)
        should_respond = result.strip().upper().startswith("YES")
        logger.debug(f"Auto-response evaluation: {should_respond}")
        return should_respond
    except Exception as e:
        logger.error(f"Error evaluating response: {e}")
        return False


async def message_processor():
    logger.info("Message processor loop started")
    while True:
        if not message_queue.empty():
            logger.debug(f"Processing {message_queue.qsize()} messages")
            while not message_queue.empty():
                msg_data = message_queue.get()
                guild_id = msg_data["guild_id"]
                channel_id = msg_data["channel_id"]
                content = msg_data["content"]
                author = msg_data["author"]
                is_mention = msg_data["is_mention"]

                logger.info(
                    f"Processing: guild={guild_id}, channel={channel_id}, "
                    f"author={author}, mention={is_mention}, content={content[:50]}"
                )

                key = f"{guild_id}_{channel_id}"
                active_users[channel_id][author] = datetime.now()

                if is_mention:
                    await process_message(guild_id, channel_id, content, author)
                elif config["channel"]["enabled"]:
                    if await evaluate_should_respond(
                        guild_id, channel_id, content, author
                    ):
                        await process_message(guild_id, channel_id, content, author)
                    else:
                        history[f"{guild_id}_{channel_id}"].append(
                            {
                                "role": "user",
                                "content": content,
                                "author": author,
                                "timestamp": datetime.now().isoformat(),
                            }
                        )
                        save_history(guild_id, channel_id)

        await asyncio.sleep(config["channel"]["loop_interval"])


@client.event
async def on_ready():
    logger.info(f"Logged in as {client.user} (ID: {client.user.id})")
    # Sync commands globally - this may take a few minutes to propagate in Discord
    synced = await tree.sync()
    logger.info(f"Synced {len(synced)} commands globally")
    for cmd in synced:
        logger.info(f"  - {cmd.name}")


@client.event
async def on_message(message):
    if message.author == client.user:
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

    message_queue.put(
        {
            "guild_id": message.guild.id if message.guild else 0,
            "channel_id": message.channel.id,
            "content": content,
            "author": message.author.name,
            "is_mention": is_mention,
        }
    )
    logger.debug(f"Queued message from {message.author.name}: {content[:50]}")


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

    with open("config.yaml", "w") as f:
        yaml.dump(config, f)

    logger.info(f"Set min_message_length to {length}")
    await interaction.response.send_message(
        f"Minimum message length set to {length} characters!", ephemeral=True
    )


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

    with open("config.yaml", "w") as f:
        yaml.dump(config, f)

    logger.info("Updated preprompt")
    await interaction.response.send_message(
        "Preprompt updated successfully!", ephemeral=True
    )


async def main():
    asyncio.create_task(message_processor())
    await client.start(DISCORD_TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
