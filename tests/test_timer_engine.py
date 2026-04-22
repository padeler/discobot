"""Tests for engine/timer_engine.py - Reminder and TimerEngine."""

import asyncio
import json
from datetime import datetime, timedelta

import pytest
from engine.timer_engine import Reminder, TimerEngine


class TestReminder:
    def test_fire_at(self):
        base = "2026-04-22T12:00:00"
        r = Reminder(channel_id=123, user="test", message="hi", delay_minutes=30, created_at=base)
        expected = datetime.fromisoformat(base) + timedelta(minutes=30)
        assert r.fire_at() == expected

    def test_time_until_seconds(self):
        now = datetime.now()
        base = now.isoformat()
        r = Reminder(channel_id=0, user="u", message="m", delay_minutes=0.0167, created_at=base)  # ~1 second
        time_str = r.time_until()
        assert "s" in time_str

    def test_time_until_minutes(self):
        now = datetime.now()
        base = now.isoformat()
        r = Reminder(channel_id=0, user="u", message="m", delay_minutes=5, created_at=base)
        time_str = r.time_until()
        assert "m" in time_str

    def test_time_until_hours(self):
        now = datetime.now()
        base = now.isoformat()
        r = Reminder(channel_id=0, user="u", message="m", delay_minutes=120, created_at=base)
        time_str = r.time_until()
        assert "h" in time_str

    def test_to_dict_from_dict_roundtrip(self):
        base = "2026-04-22T10:00:00"
        r = Reminder(channel_id=456, user="@alice", message="test msg", delay_minutes=15, created_at=base, user_id=789)
        d = r.to_dict()
        assert d["id"] == r.id
        assert d["message"] == "test msg"
        assert d["delay_minutes"] == 15
        assert d["channel_id"] == 456

        r2 = Reminder.from_dict(d)
        assert r2.message == r.message
        assert r2.delay_minutes == r.delay_minutes
        assert r2.channel_id == r.channel_id
        assert r2.id == r.id

    def test_fired_flag_persisted(self):
        d = {"id": "abc123", "channel_id": 1, "user": "u", "message": "m", "delay_minutes": 5, "created_at": "2026-01-01T00:00:00", "fired": True, "user_id": 0}
        r = Reminder.from_dict(d)
        assert r.fired is True


class TestTimerEngine:
    @pytest.mark.asyncio
    async def test_add_reminder(self, tmp_reminders):
        result = await tmp_reminders.add_reminder(
            channel_id=123, user="@bob", message="drink water", delay_minutes=30
        )
        assert result["message"] == "drink water"
        assert result["delay_minutes"] == 30
        assert "id" in result
        assert "time_until" in result

    @pytest.mark.asyncio
    async def test_add_reminder_persists(self, tmp_reminders):
        await tmp_reminders.add_reminder(channel_id=1, user="u", message="m", delay_minutes=5)
        # Load from disk
        eng2 = TimerEngine()
        import engine.timer_engine as te_module
        te_module.REMINDERS_FILE = tmp_reminders._patched_file if hasattr(tmp_reminders, '_patched_file') else te_module.REMINDERS_FILE
        await eng2.load()
        assert len(eng2.reminders) >= 1

    @pytest.mark.asyncio
    async def test_list_reminders(self, tmp_reminders):
        await tmp_reminders.add_reminder(channel_id=100, user="u1", message="m1", delay_minutes=5)
        await tmp_reminders.add_reminder(channel_id=200, user="u2", message="m2", delay_minutes=10)
        pending = await tmp_reminders.list_reminders()
        assert len(pending) == 2

    @pytest.mark.asyncio
    async def test_list_reminders_filters_by_channel(self, tmp_reminders):
        await tmp_reminders.add_reminder(channel_id=100, user="u1", message="m1", delay_minutes=5)
        await tmp_reminders.add_reminder(channel_id=200, user="u2", message="m2", delay_minutes=10)
        pending = await tmp_reminders.list_reminders(channel_id=100)
        assert len(pending) == 1
        assert pending[0]["channel_id"] == 100

    @pytest.mark.asyncio
    async def test_cancel_reminder(self, tmp_reminders):
        result = await tmp_reminders.add_reminder(channel_id=1, user="u", message="m", delay_minutes=5)
        rid = result["id"]
        canceled = await tmp_reminders.cancel_reminder(rid)
        assert canceled["message"] == "m"
        pending = await tmp_reminders.list_reminders()
        assert len(pending) == 0

    @pytest.mark.asyncio
    async def test_cancel_unknown_reminder(self, tmp_reminders):
        with pytest.raises(ValueError, match="not found"):
            await tmp_reminders.cancel_reminder("nonexistent")

    @pytest.mark.asyncio
    async def test_list_reminders_excludes_fired(self, tmp_reminders):
        r1 = await tmp_reminders.add_reminder(channel_id=1, user="u", message="m1", delay_minutes=5)
        r2 = await tmp_reminders.add_reminder(channel_id=1, user="u", message="m2", delay_minutes=10)
        # Manually mark one as fired
        for rem in tmp_reminders.reminders:
            if rem.id == r1["id"]:
                rem.fired = True
        pending = await tmp_reminders.list_reminders()
        assert len(pending) == 1
        assert pending[0]["id"] == r2["id"]

    @pytest.mark.asyncio
    async def test_save_load_roundtrip(self, tmp_reminders):
        await tmp_reminders.add_reminder(channel_id=1, user="u", message="roundtrip test", delay_minutes=5)
        await tmp_reminders.save()

        eng2 = TimerEngine()
        await eng2.load()
        assert len(eng2.reminders) == 1
        assert eng2.reminders[0].message == "roundtrip test"
