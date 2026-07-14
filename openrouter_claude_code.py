"""Harbor Claude Code adapter for OpenRouter's native Anthropic Messages API."""

from __future__ import annotations

import os

from harbor.agents.installed.base import ExecInput
from harbor.agents.installed.claude_code import ClaudeCode


class OpenRouterClaudeCode(ClaudeCode):
    """Run Claude Code against OpenRouter with native signed thinking blocks."""

    def create_run_agent_commands(self, instruction: str) -> list[ExecInput]:
        api_key = os.environ.get("OPENROUTER_API_KEY", "")
        if not api_key:
            raise ValueError("OPENROUTER_API_KEY is required")

        old_base_url = os.environ.get("ANTHROPIC_BASE_URL")
        old_auth_token = os.environ.get("ANTHROPIC_AUTH_TOKEN")
        os.environ["ANTHROPIC_BASE_URL"] = "https://openrouter.ai/api"
        os.environ["ANTHROPIC_AUTH_TOKEN"] = api_key
        try:
            commands = super().create_run_agent_commands(instruction)
        finally:
            if old_base_url is None:
                os.environ.pop("ANTHROPIC_BASE_URL", None)
            else:
                os.environ["ANTHROPIC_BASE_URL"] = old_base_url
            if old_auth_token is None:
                os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)
            else:
                os.environ["ANTHROPIC_AUTH_TOKEN"] = old_auth_token

        for command in commands:
            command.env.pop("ANTHROPIC_API_KEY", None)
            command.env["ANTHROPIC_AUTH_TOKEN"] = api_key
            command.env["ANTHROPIC_BASE_URL"] = "https://openrouter.ai/api"
            command.env["CLAUDE_CODE_EFFORT_LEVEL"] = "high"

        return commands
