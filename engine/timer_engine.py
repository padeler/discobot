"""Timer engine - stores and fires reminders, posts them to Discord channels."""

import asyncio
import json
import logging
import traceback
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

REMINDERS_FILE = Path("data/reminders.json")


class Reminder:
    def __init__(self, channel_id: int, user: str, message: str, delay_minutes: float, created_at: str, user_id: int = 0):
        self.id = str(uuid.uuid4())[:8]
        self.channel_id = channel_id
        self.user = user
        self.message = message
        self.delay_minutes = delay_minutes
        self.created_at = created_at
        self.fired = False
        self.user_id = user_id

    def fire_at(self) -> datetime:
        return datetime.fromisoformat(self.created_at) + timedelta(minutes=self.delay_minutes)

    def time_until(self) -> str:
        delta = self.fire_at() - datetime.now()
        secs = max(0, int(delta.total_seconds()))
        if secs < 60:
            return f"{secs}s"
        mins = secs // 60
        remaining_secs = secs % 60
        if mins < 60:
            return f"{mins}m {remaining_secs}s"
        hrs = mins // 60
        mins = mins % 60
        return f"{hrs}h {mins}m"

    def to_dict(self):
        return {
            "id": self.id,
            "channel_id": self.channel_id,
            "user_id": self.user_id,
            "user": self.user,
            "message": self.message,
            "delay_minutes": self.delay_minutes,
            "created_at": self.created_at,
            "fired": self.fired,
            "time_until": self.time_until(),
        }

    @classmethod
    def from_dict(cls, d):
        r = cls(d["channel_id"], d["user"], d["message"], d["delay_minutes"], d["created_at"])
        r.id = d["id"]
        r.fired = d.get("fired", False)
        r.user_id = d.get("user_id", 0)
        return r


class TimerEngine:
    def __init__(self):
        self.reminders: list[Reminder] = []
        self._monitor_task: Optional[asyncio.Task] = None
        self._on_fire_callback: Optional[Callable] = None

    def set_fire_callback(self, callback):
        self._on_fire_callback = callback

    async def load(self):
        REMINDERS_FILE.parent.mkdir(parents=True, exist_ok=True)
        if REMINDERS_FILE.exists():
            try:
                data = json.loads(REMINDERS_FILE.read_text())
                self.reminders = [Reminder.from_dict(r) for r in data]
                logger.info("Loaded %d reminders from disk", len(self.reminders))
            except (json.JSONDecodeError, KeyError, TypeError) as e:
                logger.error("Failed to load reminders: %s, starting fresh", e)
                self.reminders = []
        else:
            REMINDERS_FILE.write_text("[]")
            self.reminders = []

    async def save(self):
        REMINDERS_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = [r.to_dict() for r in self.reminders]
        REMINDERS_FILE.write_text(json.dumps(data, indent=2))

    async def add_reminder(self, channel_id: int, user: str, message: str, delay_minutes: float, user_id: int = 0, message_author_id: int = 0) -> dict:
        # Default display name when LLM omits user
        display_name = user if user else "you"
        # Parse user mention or digit to extract user_id
        parsed_user_id = user_id
        if not parsed_user_id:
            if user.startswith("<@") or user.startswith("<@!"):
                parsed_user_id = int(user.strip("<>@!>"))
            elif user.isdigit():
                parsed_user_id = int(user)
        if not parsed_user_id and message_author_id:
            parsed_user_id = message_author_id
        elif not parsed_user_id:
            parsed_user_id = channel_id

        r = Reminder(channel_id, display_name, message, delay_minutes, datetime.now().isoformat(), parsed_user_id)
        self.reminders.append(r)
        await self.save()
        logger.info("Reminder %r: '%s' in %.0f min", r.id, message, delay_minutes)
        result = r.to_dict()
        del result["fired"]
        return result

    async def list_reminders(self, channel_id: Optional[int] = None) -> list[dict]:
        if channel_id:
            pending = [r for r in self.reminders if not r.fired and r.channel_id == channel_id]
        else:
            pending = [r for r in self.reminders if not r.fired]
        return [r.to_dict() for r in pending]

    async def cancel_reminder(self, reminder_id: str) -> dict:
        for r in self.reminders:
            if r.id == reminder_id:
                self.reminders.remove(r)
                await self.save()
                logger.info("Reminder cancelled: %r", r.id)
                return r.to_dict()
        raise ValueError(f"Reminder {reminder_id} not found")

    async def _fire(self, reminder: Reminder):
        reminder.fired = True
        await self.save()
        logger.info("Reminder firing: id=%r, channel_id=%d, user=%s, user_id=%d, message=%r", 
                    reminder.id, reminder.channel_id, reminder.user, reminder.user_id, reminder.message)
        if self._on_fire_callback:
            await self._on_fire_callback(reminder)
        logger.info("Reminder fired: %r", reminder.id)

    def start_monitor(self):
        self._monitor_task = asyncio.ensure_future(self._monitor_loop())

    async def _monitor_loop(self):
        while True:
            try:
                now = datetime.now()
                for r in list(self.reminders):
                    if r.fired:
                        continue
                    if now >= r.fire_at():
                        await self._fire(r)
                await asyncio.sleep(5)
            except Exception:
                logger.error("Monitor loop error:\n%s", traceback.format_exc())
                await asyncio.sleep(5)


_engine = TimerEngine()


def get_engine():
    return _engine


def get_tools_as_string() -> str:
    return json.dumps([
        {
            "name": "add_reminder",
            "description": "Add a reminder for the user. channel_id (int), user (@mention), message (str), delay_minutes (float). Returns reminder with ID.",
        },
        {
            "name": "list_reminders",
            "description": "List all pending reminders. channel_id (int, optional) to filter.",
        },
        {
            "name": "cancel_reminder",
            "description": "Cancel a pending reminder by its reminder_id (str).",
        },
    ], indent=2)


def get_tools() -> list:
    """Return full tool definitions for the LLM."""
    return [
        {
            "type": "function",
            "function": {
                "name": "add_reminder",
                "description": "Start a timer/reminder. Either user (@mention) or user_id (int) is required, or omit both to default to the message author.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "channel_id": {"type": "integer", "description": "Discord channel ID where reminder will fire (use 0 for personal/DM reminder)"},
                        "user": {"type": "string", "description": "User mention string (e.g. @username). Omit to default to message author."},
                        "user_id": {"type": "integer", "description": "Discord user ID to remind. Omit to default to message author."},
                        "message": {"type": "string", "description": "What to remind about, e.g. 'drink water'"},
                        "delay_minutes": {"type": "number", "description": "Minutes from now until the reminder fires"},
                    },
                    "required": ["message", "delay_minutes"],
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


async def execute_timer_tool(name: str, args: dict):
    engine = get_engine()
    if name == "add_reminder":
        result = await engine.add_reminder(
            args.get("channel_id", 0),
            args.get("user", ""),
            args["message"],
            args["delay_minutes"],
            user_id=args.get("user_id", 0),
            message_author_id=args.get("message_author_id", 0),
        )
        return f"Reminder set: {result['message']} in {result['time_until']} (ID: {result['id']})"
    elif name == "list_reminders":
        reminders = await engine.list_reminders(args.get("channel_id"))
        if not reminders:
            return "No pending reminders."
        lines = [f"### Pending Reminders"]
        for r in reminders:
            lines.append(f"- {r['id']}: {r['message']} (in {r['time_until']}) -- by {r['user']}")
        return "\n".join(lines)
    elif name == "cancel_reminder":
        try:
            r = await engine.cancel_reminder(args["reminder_id"])
            return f"Cancelled: {r['message']} (ID: {r['id']})"
        except ValueError as e:
            return str(e)
    return f"Unknown timer tool: {name}"
