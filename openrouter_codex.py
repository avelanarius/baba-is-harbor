"""Harbor Codex adapter configured for OpenRouter's Responses API."""

import json
import os
import shlex

from harbor.agents.installed.base import ExecInput
from harbor.agents.installed.codex import Codex
from harbor.models.trial.paths import EnvironmentPaths


class OpenRouterCodex(Codex):
    """Run Harbor's Codex harness with an exact OpenRouter model slug."""

    def create_run_agent_commands(self, instruction: str) -> list[ExecInput]:
        if not self.model_name:
            raise ValueError("Model name is required")

        api_key = os.environ.get("OPENROUTER_API_KEY", "")
        if not api_key:
            raise ValueError("OPENROUTER_API_KEY is required")

        env = {
            "OPENROUTER_API_KEY": api_key,
            "CODEX_HOME": EnvironmentPaths.agent_dir.as_posix(),
        }

        is_openai_model = self.model_name.startswith("openai/")

        config_lines = [
            f'model = "{self.model_name}"',
            'model_provider = "openrouter"',
            f'model_reasoning_effort = "{self._reasoning_effort or "high"}"',
            "",
            "[model_providers.openrouter]",
            'name = "OpenRouter"',
            'base_url = "https://openrouter.ai/api/v1"',
            'env_key = "OPENROUTER_API_KEY"',
            'wire_api = "responses"',
        ]

        mcp_config = self._build_mcp_config_toml()
        if mcp_config:
            config_lines.extend(["", mcp_config])

        config = "\n".join(config_lines) + "\n"
        setup_command = (
            'mkdir -p "$CODEX_HOME"\n'
            f"printf %s {shlex.quote(config)} > \"$CODEX_HOME/config.toml\""
        )

        unified_exec_flag = (
            "--enable unified_exec "
            if is_openai_model
            else (
                "--disable unified_exec "
                "--disable multi_agent "
                "--disable multi_agent_v2 "
                "--disable apps "
                "--disable plugins "
            )
        )

        command = (
            "codex exec "
            "--dangerously-bypass-approvals-and-sandbox "
            "--skip-git-repo-check "
            f"--model {shlex.quote(self.model_name)} "
            "--json "
            f"{unified_exec_flag}"
            f"-c model_reasoning_effort={shlex.quote(self._reasoning_effort or 'high')} "
            "-- "
            f"{shlex.quote(instruction)} "
            f"2>&1 </dev/null | tee {EnvironmentPaths.agent_dir / self._OUTPUT_FILENAME}"
        )

        return [
            ExecInput(command=setup_command, env=env),
            ExecInput(command=command, env=env),
        ]

    def _convert_events_to_trajectory(self, session_dir):
        """Preserve current Codex ``summary_text`` objects in Harbor ATIF output.

        Harbor 0.1.44 only recognizes a reasoning summary represented as a list
        of strings. Codex 0.144 emits a list of ``summary_text`` objects instead.
        The native rollout is already lossless; this post-processes Harbor's
        derived trajectory so its readable reasoning field is lossless too.
        """
        trajectory = super()._convert_events_to_trajectory(session_dir)
        if trajectory is None:
            return None

        session_files = list(session_dir.glob("*.jsonl"))
        if not session_files:
            return trajectory

        normalized_reasoning: list[str | None] = []
        pending_reasoning: str | None = None
        pending_calls: dict[str, str | None] = {}

        with session_files[0].open() as handle:
            for line in handle:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("type") != "response_item":
                    continue

                payload = event.get("payload", {})
                payload_type = payload.get("type")
                if payload_type == "reasoning":
                    parts: list[str] = []
                    for item in payload.get("summary") or []:
                        if isinstance(item, str):
                            parts.append(item)
                        elif isinstance(item, dict) and isinstance(item.get("text"), str):
                            parts.append(item["text"])
                    if parts:
                        summary_text = "\n".join(parts)
                        pending_reasoning = (
                            f"{pending_reasoning}\n{summary_text}"
                            if pending_reasoning
                            else summary_text
                        )
                    continue

                if payload_type == "message":
                    normalized_reasoning.append(
                        pending_reasoning if payload.get("role") == "assistant" else None
                    )
                    pending_reasoning = None
                    continue

                if payload_type in {"function_call", "custom_tool_call"}:
                    call_id = payload.get("call_id")
                    if call_id:
                        pending_calls[call_id] = pending_reasoning
                    pending_reasoning = None
                    continue

                if payload_type in {"function_call_output", "custom_tool_call_output"}:
                    call_id = payload.get("call_id")
                    normalized_reasoning.append(
                        pending_calls.pop(call_id, None) if call_id else pending_reasoning
                    )
                    pending_reasoning = None

        for step, reasoning in zip(trajectory.steps, normalized_reasoning, strict=False):
            if step.source == "agent" and reasoning:
                step.reasoning_content = reasoning

        return trajectory
