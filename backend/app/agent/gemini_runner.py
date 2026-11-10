"""Gemini adapter for the agent loop: Gemini proposes function calls, SpendGuard runs them.

    send task + function declarations -> Gemini replies
      function_call parts -> run each, send all responses back in one turn, repeat
      no function calls  -> done
      safety / max tokens / step cap -> stop and report

The model's Content is appended back unchanged every turn: thought signatures
inside it must be returned exactly as received. Automatic function calling is
off, because SpendGuard, not the SDK, must run every tool.
"""

from typing import Any

from google.genai import errors, types

from .base import SYSTEM_PROMPT, Emit, maybe_json
from .tools import TOOLS, AgentTools

_BLOCKED = {"SAFETY", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "RECITATION"}


def _declarations() -> list[types.FunctionDeclaration]:
    # Same JSON Schemas the Claude adapter uses; Gemini accepts them as-is.
    return [
        types.FunctionDeclaration(
            name=t["name"],
            description=t["description"],
            parameters_json_schema=t["input_schema"],
        )
        for t in TOOLS
    ]


class GeminiAgentRunner:
    def __init__(self, client: Any, model: str, max_steps: int = 12):
        self._client = client  # google.genai.Client, or a test double with .aio.models
        self._model = model
        self._max_steps = max_steps
        self._config = types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            tools=[types.Tool(function_declarations=_declarations())],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            thinking_config=types.ThinkingConfig(include_thoughts=True),
            max_output_tokens=8192,
        )

    async def run(self, task: str, tools: AgentTools, emit: Emit) -> None:
        contents: list[types.Content] = [
            types.Content(role="user", parts=[types.Part(text=task)])
        ]
        await emit("run.started", {"task": task, "model": self._model})

        for step in range(1, self._max_steps + 1):
            try:
                response = await self._client.aio.models.generate_content(
                    model=self._model, contents=contents, config=self._config
                )
            except errors.ClientError as error:  # 4xx: bad key, bad model, quota
                hint = "rate limit or quota; try again shortly" if error.code == 429 else error.message
                await emit("run.error", {"step": step, "message": f"Gemini API error {error.code}: {hint}"})
                return
            except errors.ServerError as error:
                await emit("run.error", {"step": step, "message": f"Gemini server error {error.code}; try again."})
                return

            candidate = (response.candidates or [None])[0]
            if candidate is None or candidate.content is None:
                await emit("run.error", {"step": step, "message": "Gemini returned no answer (prompt blocked)."})
                return

            parts = candidate.content.parts or []
            for part in parts:
                if part.text and part.thought:
                    await emit("agent.thinking", {"step": step, "text": part.text})
                elif part.text:
                    await emit("agent.text", {"step": step, "text": part.text})

            contents.append(candidate.content)
            calls = [part.function_call for part in parts if part.function_call]
            finish = getattr(candidate.finish_reason, "name", None)

            if not calls:
                if finish in _BLOCKED:
                    await emit("run.error", {"step": step, "message": f"Gemini stopped: {finish}."})
                elif finish == "MAX_TOKENS":
                    await emit("run.error", {"step": step, "message": "Response hit the output token limit."})
                else:
                    await emit("run.finished", {"steps": step})
                return

            responses = []
            for call in calls:
                args = dict(call.args or {})
                await emit("tool.call", {"step": step, "tool": call.name, "input": args})
                outcome = await tools.run(call.name, args)
                await emit("tool.result", {
                    "step": step,
                    "tool": call.name,
                    "is_error": outcome.is_error,
                    "payment_id": outcome.payment_id,
                    "result": maybe_json(outcome.content),
                })
                key = "error" if outcome.is_error else "output"
                responses.append(types.Part(function_response=types.FunctionResponse(
                    id=call.id, name=call.name, response={key: maybe_json(outcome.content)},
                )))
            contents.append(types.Content(role="user", parts=responses))

        await emit("run.error", {"message": f"Stopped after {self._max_steps} steps."})
