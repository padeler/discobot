"""Skills registry - loads and manages all available skills."""

import logging
from pathlib import Path
from typing import Optional

from .parser import parse_skill_md, Skill

logger = logging.getLogger(__name__)

SKILLS_DIR = Path(__file__).parent


class SkillsRegistry:
    """Loads skills from the skills/ directory and provides a registry for trigger matching."""

    def __init__(self):
        self.skills: dict[str, Skill] = {}
        self.skills_dir: Path = SKILLS_DIR

    def load_all(self) -> int:
        """Scan skills/ directory and load all SKILL.md files."""
        self.skills.clear()
        loaded = 0

        skills_path = self.skills_dir
        if not skills_path.exists():
            logger.warning("Skills directory not found: %s", skills_path)
            skills_path.mkdir(parents=True, exist_ok=True)
            return 0

        for skill_dir in sorted(skills_path.iterdir()):
            if skill_dir.name.startswith(".") or skill_dir.name == "__pycache__":
                continue
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                continue

            try:
                skill = parse_skill_md(skill_md)
                self.skills[skill.name] = skill
                logger.info("Loaded skill: %s", skill.name)
                loaded += 1
            except Exception as e:
                logger.error("Failed to load skill from %s: %s", skill_md, e)

        if loaded:
            self._generate_index()

        logger.info("Skills registry: %d skills loaded", loaded)
        return loaded

    def get_skill(self, name: str) -> Optional[Skill]:
        """Get a skill by name."""
        return self.skills.get(name)

    def get_all_descriptions(self) -> dict[str, str]:
        """Return a dict of {name: description} for all loaded skills."""
        return {name: skill.description for name, skill in self.skills.items()}

    def get_all_names(self) -> list[str]:
        """Return all skill names for slash command registration."""
        return list(self.skills.keys())

    def _generate_index(self):
        """Generate SKILLS.md index file for human readability."""
        index_path = self.skills_dir / "SKILLS.md"
        lines = ["# Available Skills\n", "---\n"]

        for name, skill in self.skills.items():
            lines.append(f"- **{name}**: {skill.description}")
            lines.append("")

        index_path.write_text("\n".join(lines), encoding="utf-8")
        logger.debug("Generated skills index at %s", index_path)


# Singleton instance
_registry: Optional[SkillsRegistry] = None


def get_registry() -> SkillsRegistry:
    global _registry
    if _registry is None:
        _registry = SkillsRegistry()
    return _registry
