"""Tests for engine/parser.py - SKILL.md parsing."""

import pytest
from pathlib import Path

from engine.parser import parse_skill_md, Skill


def test_parse_valid_skill(tmp_path):
    skill_md = tmp_path / "my-skill" / "SKILL.md"
    skill_md.parent.mkdir()
    skill_md.write_text(
        "---\nname: my-skill\ndescription: A test skill\nversion: 2.0.0\nlicense: MIT\n---\nBody content",
        encoding="utf-8",
    )
    skill = parse_skill_md(skill_md)
    assert skill.name == "my-skill"
    assert skill.description == "A test skill"
    assert skill.body == "Body content"
    assert skill.version == "2.0.0"
    assert skill.license == "MIT"


def test_parse_missing_frontmatter(tmp_path):
    skill_md = tmp_path / "bad" / "SKILL.md"
    skill_md.parent.mkdir()
    skill_md.write_text("No frontmatter here", encoding="utf-8")
    with pytest.raises(ValueError, match="missing valid YAML frontmatter"):
        parse_skill_md(skill_md)


def test_parse_missing_name(tmp_path):
    skill_md = tmp_path / "no-name" / "SKILL.md"
    skill_md.parent.mkdir()
    skill_md.write_text(
        "---\ndescription: No name field\n---\nbody",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="missing required field"):
        parse_skill_md(skill_md)


def test_parse_version_and_license(tmp_path):
    skill_md = tmp_path / "versioned" / "SKILL.md"
    skill_md.parent.mkdir()
    skill_md.write_text(
        "---\nname: versioned\ndescription: Has version\nversion: 3.1.0\nlicense: Apache-2.0\nmetadata: {author: test}\n---\nSome body",
        encoding="utf-8",
    )
    skill = parse_skill_md(skill_md)
    assert skill.version == "3.1.0"
    assert skill.license == "Apache-2.0"
    assert skill.metadata == {"author": "test"}
