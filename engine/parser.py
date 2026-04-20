"""Parse SKILL.md files following the Agent Skills spec."""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml


@dataclass
class Skill:
    name: str
    description: str
    body: str
    version: Optional[str] = None
    license: Optional[str] = None
    metadata: Optional[dict] = None


def parse_skill_md(filepath: Path) -> Skill:
    """Parse a SKILL.md file and return a Skill object."""
    content = filepath.read_text(encoding="utf-8")

    match = re.match(r"^---\n(.*?)\n---\n(.*)$", content, re.DOTALL | re.MULTILINE)
    if not match:
        raise ValueError(f"SKILL.md {filepath} is missing valid YAML frontmatter")

    frontmatter = yaml.safe_load(match.group(1))
    body = match.group(2).strip()

    required = frontmatter.get("name"), frontmatter.get("description")
    for field in required:
        if not field:
            raise ValueError(f"SKILL.md {filepath} is missing required field: name, description")

    name = Path(filepath.parent).name
    return Skill(
        name=name,
        description=frontmatter.get("description", ""),
        body=body,
        version=frontmatter.get("version"),
        license=frontmatter.get("license"),
        metadata=frontmatter.get("metadata"),
    )
