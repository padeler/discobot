"""Shared fixtures for all tests."""

import shutil
from pathlib import Path

import pytest

SKILL_GENERAL = """---
name: general
description: General conversation and assistance. Use for everyday questions, explanations, and general knowledge.
version: 1.0.0
---

You are a helpful assistant.
"""

SKILL_TIMER = """---
name: timer-reminder
description: Set timers and reminders. Use when the user wants to be reminded about something later.
version: 1.0.0
---

Set a timer or reminder.
"""

SKILL_WEB = """---
name: web-search
description: Search the web for current information. Use when the user asks for up-to-date information, news, or research.
version: 1.0.0
---

Search the web.
"""

SKILL_MEMORY = """---
name: memory
description: Remember and recall facts. Use when the user asks you to remember something.
version: 1.0.0
---

Remember or recall information.
"""


@pytest.fixture
def test_skills_dir(tmp_path):
    """Create a temporary directory with SKILL.md files."""
    skills = {
        "general": SKILL_GENERAL,
        "timer-reminder": SKILL_TIMER,
        "web-search": SKILL_WEB,
        "memory": SKILL_MEMORY,
    }
    for name, content in skills.items():
        (tmp_path / name).mkdir(parents=True, exist_ok=True)
        (tmp_path / name / "SKILL.md").write_text(content, encoding="utf-8")
    return tmp_path


@pytest.fixture
def clean_registry(test_skills_dir):
    """Reset the singleton SkillsRegistry and load from test_skills_dir."""
    from engine.registry import SkillsRegistry, _registry as existing

    reg = SkillsRegistry()
    reg.skills_dir = test_skills_dir
    loaded = reg.load_all()

    import engine.registry as registry_module

    registry_module._registry = reg
    yield reg

    registry_module._registry = existing


@pytest.fixture
def tmp_reminders(tmp_path):
    """Return a TimerEngine using a temp data directory."""
    from engine.timer_engine import TimerEngine

    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    import engine.timer_engine as te_module

    orig = te_module.REMINDERS_FILE
    te_module.REMINDERS_FILE = data_dir / "reminders.json"

    eng = TimerEngine()
    # Store orig on the engine for test use
    eng._orig_file = orig
    yield eng

    te_module.REMINDERS_FILE = orig


@pytest.fixture
def tmp_memories(tmp_path):
    """Return a MemoryEngine using a temp data directory."""
    from engine.memory_engine import MemoryEngine

    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    import engine.memory_engine as me_module

    orig = me_module.MEMORIES_FILE
    me_module.MEMORIES_FILE = data_dir / "memories.json"

    eng = MemoryEngine()
    eng._orig_file = orig
    yield eng

    me_module.MEMORIES_FILE = orig


@pytest.fixture
def sample_messages():
    """Sample (message, expected_skill_name) tuples for trigger testing."""
    return [
        ("set a reminder for tomorrow", "timer-reminder"),
        ("search the web for news", "web-search"),
        ("remember this fact", "memory"),
        ("help me with something", "general"),
    ]
