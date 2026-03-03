"""Tests for the system prompt builder."""

from src.agent.system_prompt import build_system_prompt, CORE_IDENTITY


class TestBuildSystemPrompt:
    def test_core_identity_included(self):
        prompt = build_system_prompt()
        assert "NeuralDebug" in prompt
        assert "debugging" in prompt.lower()

    def test_extra_context(self):
        prompt = build_system_prompt(extra_context="Always use Python 3.12")
        assert "Python 3.12" in prompt

    def test_skill_prompts(self):
        skills = {
            "memory-debugger": "Use Valgrind for memory leak detection.",
            "perf-profiler": "Use perf for CPU profiling.",
        }
        prompt = build_system_prompt(skill_prompts=skills)
        assert "memory-debugger" in prompt
        assert "Valgrind" in prompt
        assert "perf-profiler" in prompt

    def test_empty_skills(self):
        prompt = build_system_prompt(skill_prompts={})
        # Should not include skills section header
        assert "Installed Skills" not in prompt

    def test_all_combined(self):
        prompt = build_system_prompt(
            extra_context="Custom context",
            skill_prompts={"my-skill": "Do something"},
        )
        assert "NeuralDebug" in prompt
        assert "Custom context" in prompt
        assert "my-skill" in prompt
