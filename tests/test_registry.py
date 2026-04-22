"""Tests for engine/registry.py - SkillsRegistry."""


def test_load_all_finds_skills(clean_registry, test_skills_dir):
    assert len(clean_registry.skills) == 4
    assert "general" in clean_registry.skills
    assert "timer-reminder" in clean_registry.skills
    assert "web-search" in clean_registry.skills
    assert "memory" in clean_registry.skills


def test_get_skill(clean_registry):
    skill = clean_registry.get_skill("general")
    assert skill is not None
    assert skill.name == "general"
    assert "General conversation" in skill.description


def test_get_unknown_skill(clean_registry):
    assert clean_registry.get_skill("nonexistent") is None


def test_get_all_names_and_descriptions(clean_registry):
    descs = clean_registry.get_all_descriptions()
    assert len(descs) == 4
    assert "general" in descs
    assert "web-search" in descs
    assert "General conversation" in descs["general"]


def test_get_all_names(clean_registry):
    names = clean_registry.get_all_names()
    assert len(names) == 4
    assert "general" in names


def test_generate_index(clean_registry, test_skills_dir):
    index_file = test_skills_dir / "SKILLS.md"
    assert index_file.exists()
    content = index_file.read_text()
    assert "**general**" in content
    assert "**web-search**" in content
    assert "**timer-reminder**" in content
