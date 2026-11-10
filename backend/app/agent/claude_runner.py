"""Claude adapter for the agent loop: Claude proposes tool calls, SpendGuard runs them.

    send task + tools -> Claude replies
      tool_use  -> run each tool, send all results back in one message, repeat
      end_turn  -> done
      refusal / max_tokens / step cap -> stop and report

The full response.content is appended back unchanged every turn: thinking
blocks must be returned exactly as received.
"""

from typing import Any

import anthropic

from .base import SYSTEM_PROMPT, Emit, maybe_json
from .tools import TOOLS, AgentTools

class ClaudeAgentRunner:
    def __init__(
        self,
        client: Any,  # anthropic.AsyncAnthropic, or a test double with .beta.messages.create
        model: str = "claude-opus-5-5",
        effort: str = "medium",
        max_steps: int = 12,
    ):
        self._client = client
        self._model = model
        self._effort = effort
        self._max_steps = max_steps

    async def run(self, task: str, tools: AgentTools, emit: Emit) -> None:
        messages: list[dict[str, Any]] = [{"role": "user", "content": task}]
        await emit("run.started", {"task": task, "model": self._model})

        for step in range(1, self._max_steps + 1):
            try:
                response = await self._client.beta.messages.create(
                    model=self._model,
                    max_tokens=16000,
                    system=SYSTEM_PROMPT,
                    tools=TOOLS,
                    messages=messages,
                    thinking={"type": "adaptive", "display": "summarized"},
                    output_config={"effort": self._effort},
                    # On a safety decline, the API retries on a fallback model.
                    betas=["server-side-fallback-2026-07-01"],
                    fallbacks="default",
                )
            except anthropic.RateLimitError:
                await emit("run.error", {"step": step, "message": "Claude rate limit; try again shortly."})
                return
            except anthropic.APIStatusError as error:
                await emit("run.error", {"step": step, "message": f"Claude API error {error.status_code}: {error.message}"})
                return
            except anthropic.APIConnectionError:
                await emit("run.error", {"step": step, "message": "Could not reach the Claude API."})
                return

            for block in response.content:
                if block.type == "thinking" and block.thinking:
                    await emit("agent.thinking", {"step": step, "text": block.thinking})
                elif block.type == "text" and block.text:
                    await emit("agent.text", {"step": step, "text": block.text})

            messages.append({"role": "assistant", "content": response.content})

            match response.stop_reason:
                case "end_turn":
                    await emit("run.finished", {"steps": step})
                    return
                case "pause_turn":
                    continue
                case "refusal":
                    details = getattr(response, "stop_details", None)
                    await emit("run.error", {
                        "step": step,
                        "message": "Claude declined this task.",
                        "category": getattr(details, "category", None),
                    })
                    return
                case "max_tokens":
                    await emit("run.error", {"step": step, "message": "Response hit max_tokens."})
                    return

            # stop_reason == "tool_use": run every call, return all results together.
            results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                await emit("tool.call", {"step": step, "tool": block.name, "input": block.input})
                outcome = await tools.run(block.name, block.input)
                await emit("tool.result", {
                    "step": step,
                    "tool": block.name,
                    "is_error": outcome.is_error,
                    "payment_id": outcome.payment_id,
                    "result": maybe_json(outcome.content),
                })
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": outcome.content,
                    "is_error": outcome.is_error,
                })
            messages.append({"role": "user", "content": results})

        await emit("run.error", {"message": f"Stopped after {self._max_steps} steps."})

