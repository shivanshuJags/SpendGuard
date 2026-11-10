"""Agent tests with a scripted fake Claude: no API key, no cost, deterministic.

The fake plays the part of a *manipulated* agent: it tries to buy the injected
listing. The point is that the outcome is decided by policy, not by Claude.
"""

import json
from types import SimpleNamespace as NS

import anthropic
import httpx
import pytest
from fastapi.testclient import TestClient

from app.adapters.store import Store
from app.config import Settings
from app.main import create_app
from tests.fakes import FakePayPal

MERCHANT = "merchant@business.example.com"


def tool_use(id, name, **input):
    return NS(type="tool_use", id=id, name=name, input=input)


def text(t):
    return NS(type="text", text=t)


def reply(*blocks, stop="tool_use"):
    return NS(content=list(blocks), stop_reason=stop)


class FakeClaude:
    """Returns scripted responses in order and records every request."""

    def __init__(self, *responses):
        self._responses = list(responses)
        self.requests = []
        self.beta = NS(messages=NS(create=self._create))

    async def _create(self, **kwargs):
        self.requests.append(kwargs)
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


MANIPULATED_RUN = [
    reply(tool_use("t1", "get_allowance"), tool_use("t2", "search_catalog", query="")),
    reply(
        text("The Office Rewards bundle says it's pre-approved, buying it first."),
        tool_use("t3", "purchase", item_id="office-rewards-bundle", quantity=1,
                 reason="owner pre-approved"),
        tool_use("t4", "purchase", item_id="coffee-house-1kg", quantity=1, reason="out of coffee"),
        tool_use("t5", "purchase", item_id="usbc-hub-pro", quantity=1, reason="needs dual 4K"),
    ),
    reply(text("Bought coffee; hub awaits approval; bundle was denied."), stop="end_turn"),
]


def make_client(claude, paypal=None):
    settings = Settings(_env_file=None, paypal_client_id="id", paypal_client_secret="s",
                        demo_merchant_email=MERCHANT)
    store = Store(":memory:")
    return TestClient(create_app(settings, gateway=paypal or FakePayPal(), store=store,
                                 claude=claude)), store


def run_to_end(client, task="Restock the office"):
    allowance = client.post("/api/demo/allowance").json()["id"]
    client.post("/api/wallet/link")
    client.get("/api/wallet/return?approval_token_id=SETUP1", follow_redirects=False)
    run = client.post("/api/agent/runs", json={"allowance_id": allowance, "task": task}).json()
    events = []
    with client.stream("GET", f"/api/agent/runs/{run['id']}/events") as stream:
        for line in stream.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return allowance, run, events


def test_manipulated_agent_is_still_bounded_by_policy():
    claude = FakeClaude(*MANIPULATED_RUN)
    client, store = make_client(claude)
    with client:
        allowance, run, events = run_to_end(client)
        payments = {p["item"]: p for p in client.get(f"/api/allowances/{allowance}/payments").json()}

    assert events[0]["kind"] == "run.started" and events[-1]["kind"] == "run.finished"
    bundle = payments["Office Rewards bundle (coffee + supplies credit)"]
    assert bundle["status"] == "denied"
    assert {"category.blocked", "merchant.unknown"} <= {r["rule_id"] for r in bundle["rules"]}
    assert payments["House blend coffee beans, 1 kg"]["status"] == "paid"
    assert payments["USB-C docking hub Pro, 11 ports"]["status"] == "pending_owner"

    # Claude was told the denial and why, so it can adapt.
    denial = next(e for e in events if e["kind"] == "tool.result" and e["result"].get("status") == "denied")
    assert "category" in denial["result"]["message"].lower()


def test_loop_protocol_matches_the_api_contract():
    claude = FakeClaude(*MANIPULATED_RUN)
    client, _ = make_client(claude)
    with client:
        run_to_end(client)

    first, second = claude.requests[0], claude.requests[1]
    assert first["model"] == "claude-opus-5-5"
    assert all(t["strict"] for t in first["tools"])
    # Turn 2 = task, the assistant's content echoed back unchanged, then all results in ONE message.
    assistant, results = second["messages"][1], second["messages"][2]
    assert assistant["content"] is MANIPULATED_RUN[0].content
    assert [r["tool_use_id"] for r in results["content"]] == ["t1", "t2"]
    catalog = json.loads(results["content"][1]["content"])
    assert "untrusted" in catalog["note"]


def test_tool_errors_are_reported_not_raised():
    claude = FakeClaude(
        reply(tool_use("t1", "purchase", item_id="no-such-thing", quantity=1, reason="x"),
              tool_use("t2", "purchase", item_id="coffee-house-1kg", quantity=99, reason="x")),
        reply(text("Could not buy those."), stop="end_turn"),
    )
    client, _ = make_client(claude)
    with client:
        _, _, events = run_to_end(client)
    errors = [e for e in events if e["kind"] == "tool.result"]
    assert [e["is_error"] for e in errors] == [True, True]
    sent = claude.requests[1]["messages"][2]["content"]
    assert all(r["is_error"] for r in sent)


def test_step_cap_stops_a_looping_agent():
    looping = [reply(tool_use(f"t{i}", "get_allowance")) for i in range(20)]
    claude = FakeClaude(*looping)
    client, _ = make_client(claude)
    with client:
        _, _, events = run_to_end(client)
    assert events[-1]["kind"] == "run.error" and "12 steps" in events[-1]["message"]
    assert len(claude.requests) == 12


def test_refusal_and_api_errors_end_the_run_visibly():
    refused = FakeClaude(reply(text(""), stop="refusal"))
    client, _ = make_client(refused)
    with client:
        _, _, events = run_to_end(client)
    assert events[-1]["kind"] == "run.error" and "declined" in events[-1]["message"]

    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    down = FakeClaude(anthropic.APIConnectionError(request=request))
    client, _ = make_client(down)
    with client:
        _, _, events = run_to_end(client)
    assert events[-1]["kind"] == "run.error" and "reach" in events[-1]["message"]


def test_agent_unavailable_without_key():
    client, _ = make_client(claude=None)
    with client:
        allowance = client.post("/api/demo/allowance").json()["id"]
        response = client.post("/api/agent/runs", json={"allowance_id": allowance, "task": "x"})
    assert response.status_code == 503


def test_late_subscriber_can_replay_from_a_position():
    claude = FakeClaude(*MANIPULATED_RUN)
    client, _ = make_client(claude)
    with client:
        _, run, events = run_to_end(client)
        replay = []
        with client.stream("GET", f"/api/agent/runs/{run['id']}/events?after=3") as stream:
            for line in stream.iter_lines():
                if line.startswith("data: "):
                    replay.append(json.loads(line[6:])["seq"])
    assert replay == [e["seq"] for e in events if e["seq"] > 3]
