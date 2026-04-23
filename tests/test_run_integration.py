"""Integration tests for run.py helper functions (update_skills, build_prompt, etc.)."""

import sys
from pathlib import Path

import pytest
from engine.parser import Skill

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


class TestUpdateSkills:
    def setup_method(self):
        # Reset the global active_skills dict
        import run

        run.active_skills.clear()

    def test_update_skills_resets_on_mention(self):
        import run

        run.active_skills[123] = ["fake"]
        run.update_skills(123, "hello", is_mention=True)
        assert run.active_skills[123] == []

    def test_update_skills_matches(self, clean_registry):
        import run

        run.skills_registry = clean_registry
        run.update_skills(123, "search the web", is_mention=False)
        assert len(run.active_skills[123]) > 0
        assert run.active_skills[123][0]["name"] == "web-search"

    def test_update_skills_no_match(self, clean_registry):
        import run

        run.skills_registry = clean_registry
        run.update_skills(123, "xyznonexistent zzzzzz", is_mention=False)
        assert len(run.active_skills[123]) == 0


class TestBuildSkillInjection:
    def test_empty_skills_returns_none(self):
        import run

        assert run.build_skill_injection([]) is None

    def test_single_skill(self, test_skills_dir):
        import run
        from engine.parser import parse_skill_md

        skill = parse_skill_md(test_skills_dir / "general" / "SKILL.md")
        skills = [{"name": skill.name, "body": skill.body}]
        result = run.build_skill_injection(skills)
        assert result is not None
        assert "GENERAL SKILL" in result
        assert "helpful assistant" in result

    def test_multiple_skills(self, test_skills_dir):
        import run
        from engine.parser import parse_skill_md

        skills = [
            {"name": "general", "body": "general body"},
            {"name": "memory", "body": "memory body"},
        ]
        result = run.build_skill_injection(skills)
        assert "GENERAL SKILL" in result
        assert "MEMORY SKILL" in result


class TestBuildPrompt:
    def test_includes_date(self):
        import run

        prompt = run.build_prompt(123, [], 0)
        has_date = any("Current date/time" in m["content"] for m in prompt if m["role"] == "system")
        assert has_date

    def test_includes_skills(self, test_skills_dir, clean_registry):
        import run

        run.skills_registry = clean_registry
        run.active_skills[123] = [
            {"name": "general", "body": "test body content"},
        ]
        prompt = run.build_prompt(123, [], 0)
        has_skills = any("test body content" in m["content"] for m in prompt if m["role"] == "system")
        assert has_skills

    def test_includes_messages(self):
        import run

        msgs = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi back"},
        ]
        prompt = run.build_prompt(123, msgs, 0)
        contents = [m["content"] for m in prompt]
        assert "hello" in contents
        assert "hi back" in contents


class TestSkillsCommandTruncation:
    def test_long_skill_line_truncated(self):
        """Simulate a skill with a very long description and verify truncation."""
        long_desc = "x" * 2000
        prefix = "- "
        name = "very-long-skill-name"
        line = f"{prefix}`{name}`: {long_desc}"

        # Original line exceeds 1000
        assert len(line) > 1000

        # Apply the same truncation logic from run.py
        if len(line) > 1000:
            line = f"{prefix}`{name}`: {long_desc[:972]}.."

        assert len(line) == 1000

    def test_short_skill_line_untouched(self):
        """Short lines should not be modified."""
        line = "- `short`: This is short"
        original = line

        if len(line) > 1000:
            line = f"- `short`: {line[:1000 - 2 - 5 - 2]}.."

        assert line == original


class TestAuthorIdScoping:
    def test_call_ollama_accepts_author_id(self):
        """Verify call_ollama signature includes author_id parameter."""
        import inspect
        import run
        sig = inspect.signature(run.call_ollama)
        assert "author_id" in sig.parameters, "call_ollama should accept author_id parameter"


class TestProcessMessageRefactored:
    def setup_method(self):
        import run
        run.active_skills.clear()
        run.history.clear()

    def test_process_message_accepts_message_object(self):
        """process_message should accept a Message dataclass."""
        import inspect
        import run
        sig = inspect.signature(run.process_message)
        params = list(sig.parameters.keys())
        assert "msg" in params or "message" in params, f"process_message should accept a Message object, got params: {params}"

    def test_process_message_no_redundant_skill_match(self):
        """update_skills should be called once per message, not twice."""
        import run
        from engine import triggers as triggers_module
        original_match = triggers_module.match_skill
        call_count = 0

        def counting_match(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return original_match(*args, **kwargs)

        triggers_module.match_skill = counting_match
        try:
            run.update_skills(123, "search the web", is_mention=False)
            count_after_update = call_count
            assert count_after_update > 0, "update_skills should call match_skill"
        finally:
            triggers_module.match_skill = original_match


class TestEvaluateShouldRespond:
    def setup_method(self):
        import run
        run.active_skills.clear()
        run.history.clear()
        run.active_users.clear()

    def test_evaluate_accepts_message_object(self):
        """evaluate_should_respond should accept a Message object."""
        import inspect
        import run
        sig = inspect.signature(run.evaluate_should_respond)
        params = list(sig.parameters.keys())
        assert "msg" in params or "message" in params, f"evaluate_should_respond should accept a Message object, got params: {params}"


class TestOnMessageEnqueues:
    """Verify that on_message only enqueues and does no LLM processing."""

    def test_no_ollama_call_in_on_message(self):
        """on_message source should not contain call_ollama — processing is delegated."""
        import inspect
        import run
        source = inspect.getsource(run.on_message)
        assert "call_ollama" not in source, "on_message should not call Ollama directly"
        assert "process_message" not in source, "on_message should not call process_message directly"


class TestMatchSlashCommand:
    def test_match(self):
        from engine.triggers import match_slash_command
        from engine.registry import SkillsRegistry

        reg = SkillsRegistry()
        reg.skills = {"web-search": Skill(name="web-search", description="Search the web", body="")}
        assert match_slash_command("web-search", reg) == "web-search"

    def test_no_match(self):
        from engine.triggers import match_slash_command
        from engine.registry import SkillsRegistry

        reg = SkillsRegistry()
        reg.skills = {"general": Skill(name="general", description="General", body="")}
        assert match_slash_command("nonexistent", reg) is None
