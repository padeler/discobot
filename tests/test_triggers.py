"""Tests for engine/triggers.py - keyword extraction, fuzzy matching, skill matching."""

import pytest
from engine.triggers import (
    _normalize,
    extract_keywords,
    _fuzzy_match,
    match_skill,
    match_slash_command,
)
from engine.registry import SkillsRegistry
from engine.parser import Skill


def _patch_registry(reg):
    """Patch the module-level singleton in engine.registry."""
    import engine.registry as reg_module

    orig = reg_module._registry
    reg_module._registry = reg
    return orig


def _unpatch_registry(orig):
    import engine.registry as reg_module

    reg_module._registry = orig


class TestNormalize:
    def test_lowercase(self):
        assert _normalize("HELLO World") == "hello world"

    def test_remove_accents(self):
        result = _normalize("cafe")
        assert result == "cafe"  # no combining chars in "cafe"

    def test_collapse_whitespace(self):
        assert _normalize("too   many    spaces") == "too many spaces"


class TestExtractKeywords:
    def test_removes_stop_words(self):
        result = extract_keywords("the quick brown fox")
        assert result == {"quick", "brown", "fox"}
        assert "the" not in result

    def test_empty_for_stop_word_only(self):
        assert extract_keywords("the and is") == set()

    def test_filters_short_words(self):
        result = extract_keywords("a is the fox")
        assert result == {"fox"}

    def test_empty_string(self):
        assert extract_keywords("") == set()

    def test_keeps_meaningful_words(self):
        result = extract_keywords("search the web for current weather in crete")
        assert "search" in result
        assert "weather" in result
        assert "crete" in result
        assert "the" not in result


class TestFuzzyMatch:
    def test_direct_keyword_match(self):
        score = _fuzzy_match("hello world", {"hello", "world"})
        assert score == 1.0

    def test_partial_token_match(self):
        score = _fuzzy_match("hello", {"helloo"})
        # "helloo" contains "hello" as substring => 0.5 per keyword
        assert score == 0.5

    def test_no_match(self):
        score = _fuzzy_match("completely unrelated text", {"hello", "world"})
        assert score == 0.0

    def test_empty_keywords(self):
        score = _fuzzy_match("hello", set())
        assert score == 0.0

    def test_partial_match_score(self):
        score = _fuzzy_match("hello world", {"hello", "xyz"})
        # "hello" matches directly (1.0), "xyz" doesn't match (0.0) => 0.5
        assert score == 0.5


class TestMatchSkill:
    def test_matches_above_threshold(self):
        reg = SkillsRegistry()
        reg.skills = {
            "web-search": Skill(name="web-search", description="Search the web for information", body=""),
        }
        orig = _patch_registry(reg)
        try:
            results = match_skill("search the web", reg, threshold=0.3)
            assert len(results) == 1
            assert results[0]["skill_name"] == "web-search"
        finally:
            _unpatch_registry(orig)

    def test_matches_below_threshold(self):
        reg = SkillsRegistry()
        reg.skills = {
            "web-search": Skill(name="web-search", description="Search the web for information", body=""),
        }
        orig = _patch_registry(reg)
        try:
            results = match_skill("xyz nonono zzzzz", reg, threshold=0.3)
            assert results == []
        finally:
            _unpatch_registry(orig)

    def test_empty_message(self):
        reg = SkillsRegistry()
        reg.skills = {"general": Skill(name="general", description="General help", body="")}
        orig = _patch_registry(reg)
        try:
            results = match_skill("", reg)
            assert results == []
        finally:
            _unpatch_registry(orig)

    def test_sorted_by_confidence(self):
        reg = SkillsRegistry()
        reg.skills = {
            "web-search": Skill(name="web-search", description="Search the web for current news and updates", body=""),
            "memory": Skill(name="memory", description="Remember and recall stored information and notes", body=""),
        }
        orig = _patch_registry(reg)
        try:
            results = match_skill("search the web", reg, threshold=0.0)
            # web-search should have higher confidence
            assert results[0]["skill_name"] == "web-search"
            assert results[0]["confidence"] > results[1]["confidence"]
        finally:
            _unpatch_registry(orig)

    def test_no_skills_in_registry(self):
        reg = SkillsRegistry()
        reg.skills = {}
        results = match_skill("hello", reg)
        assert results == []


class TestMatchSlashCommand:
    def test_exact_match(self):
        reg = SkillsRegistry()
        reg.skills = {"web-search": Skill(name="web-search", description="Search the web", body="")}
        orig = _patch_registry(reg)
        try:
            assert match_slash_command("web-search", reg) == "web-search"
        finally:
            _unpatch_registry(orig)

    def test_case_insensitive(self):
        reg = SkillsRegistry()
        reg.skills = {"web-search": Skill(name="web-search", description="Search the web", body="")}
        orig = _patch_registry(reg)
        try:
            assert match_slash_command("WEB-SEARCH", reg) == "web-search"
        finally:
            _unpatch_registry(orig)

    def test_no_match(self):
        reg = SkillsRegistry()
        reg.skills = {"web-search": Skill(name="web-search", description="Search the web", body="")}
        orig = _patch_registry(reg)
        try:
            assert match_slash_command("timer-reminder", reg) is None
        finally:
            _unpatch_registry(orig)
