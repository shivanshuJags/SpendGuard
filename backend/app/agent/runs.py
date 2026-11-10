"""In-memory registry of agent runs and their event streams.

A run executes as a background task. Its events are kept in a list so a client
that connects late (or reconnects) can replay from any position, then follow
live. Payments themselves are persisted by the payment service; run events
live only as long as the process, which is fine for a demo.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from app.adapters.store import Store, utcnow

from .base import AgentRunner
from .tools import AgentTools

_TERMINAL = {"run.finished", "run.error"}
# Persisted to the audit log; per-step chatter is not.
_AUDITED = {"run.started", "run.finished", "run.error"}


@dataclass
class AgentRun:
    id: str
    allowance_id: str
    task: str
    status: str = "running"  # running | finished | error
    events: list[dict[str, Any]] = field(default_factory=list)
    changed: asyncio.Condition = field(default_factory=asyncio.Condition)


class RunRegistry:
    def __init__(self, store: Store):
        self._store = store
        self._runs: dict[str, AgentRun] = {}
        self._tasks: set[asyncio.Task] = set()  # keep references so tasks aren't GC'd

    def get(self, run_id: str) -> AgentRun | None:
        return self._runs.get(run_id)

    def start(self, runner: AgentRunner, tools: AgentTools, allowance_id: str, task: str) -> AgentRun:
        run = AgentRun(id=f"run_{uuid.uuid4().hex[:12]}", allowance_id=allowance_id, task=task)
        self._runs[run.id] = run

        async def emit(kind: str, data: dict[str, Any]) -> None:
            event = {"seq": len(run.events) + 1, "at": utcnow().isoformat(), "kind": kind, **data}
            if kind == "run.finished":
                run.status = "finished"
            elif kind == "run.error":
                run.status = "error"
            if kind in _AUDITED:
                self._store.append_audit(f"agent.{kind}", {"run_id": run.id, **data})
            async with run.changed:
                run.events.append(event)
                run.changed.notify_all()

        async def main() -> None:
            try:
                await runner.run(task, tools, emit)
            except Exception as error:  # surface bugs to the dashboard instead of hanging it
                await emit("run.error", {"message": f"Agent crashed: {type(error).__name__}: {error}"})

        background = asyncio.create_task(main())
        self._tasks.add(background)
        background.add_done_callback(self._tasks.discard)
        return run

    async def follow(self, run: AgentRun, after: int = 0) -> AsyncIterator[dict[str, Any]]:
        """Yield events with seq > after, then live ones, until the run ends."""
        position = after
        while True:
            async with run.changed:
                await run.changed.wait_for(lambda: len(run.events) > position)
                fresh = run.events[position:]
            for event in fresh:
                position = event["seq"]
                yield event
                if event["kind"] in _TERMINAL:
                    return

    async def shutdown(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
