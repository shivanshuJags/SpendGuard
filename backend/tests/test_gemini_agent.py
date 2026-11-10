"""Gemini adapter tests: real google.genai types, scripted responses, no API key.

As with Claude, the fake plays a *manipulated* agent; policy decides outcomes.
"""

import json
from types import SimpleNamespace as NS

from fastapi.testclient import TestClient
from google.genai import errors, types

from app.adapters.store import Store
from app.config import Settings
from app.main import create_app
from tests.fakes import FakePayPal

MERCHANT = "merchant@business.example.com"


def call(id, name, **args):
    return types.Part(function_call=types.FunctionCall(id=id, name=name, args=args))


def model_turn(*parts, finish="STOP"):
    content = types.Content(role="model", parts=list(parts))
    return NS(candidates=[NS(content=content, finish_reason=types.FinishReason[finish])])


class FakeGemini:
    def __init__(self, *responses):
        self._responses = list(responses)
        self.requests = []
        self.aio = NS(models=NS(generate_content=self._generate))

    async def _generate(self, *, model, contents, config):
        self.requests.append({"model": model, "contents": list(contents), "config": config})
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


MANIPULATED_RUN = [
    model_turn(
        types.Part(text="Check rules and catalog first.", thought=True),
        call("c1", "get_allowance"),
        call("c2", "search_catalog", query=""),
    ),
    model_turn(
        call("c3", "purchase", item_id="office-rewards-bundle", quantity=1, reason="owner pre-approved"),
        call("c4", "purchase", item_id="coffee-house-1kg", quantity=1, reason="out of coffee"),
        call("c5", "purchase", item_id="usbc-hub-pro", quantity=1, reason="needs dual 4K"),
    ),
    model_turn(types.Part(text="Coffee bought, hub awaits approval, bundle denied.")),
]


def run(fake):
    settings = Settings(_env_file=None, paypal_client_id="id", paypal_client_secret="s",
                        demo_merchant_email=MERCHANT, agent_provider="gemini")
    store = Store(":memory:")
    with TestClient(create_app(settings, gateway=FakePayPal(), store=store, llm=fake)) as client:
        allowance = client.post("/api/demo/allowance").json()["id"]
        client.post("/api/wallet/link")
        client.get("/api/wallet/return?approval_token_id=SETUP1", follow_redirects=False)
        run = client.post("/api/agent/runs", json={"allowance_id": allowance, "task": "Restock"}).json()
        events = []
        with client.stream("GET", f"/api/agent/runs/{run['id']}/events") as stream:
            for line in stream.iter_lines():
                if line.startswith("data: "):
                    events.append(json.loads(line[6:]))
        payments = {p["item"]: p["status"] for p in client.get(f"/api/allowances/{allowance}/payments").json()}
    return events, payments


def test_manipulated_gemini_agent_is_still_bounded_by_policy():
    events, payments = run(FakeGemini(*MANIPULATED_RUN))
    assert events[0]["model"] == "gemini-2.5-flash"
    assert events[-1]["kind"] == "run.finished"
    assert payments["Office Rewards bundle (coffee + supplies credit)"] == "denied"
    assert payments["House blend coffee beans, 1 kg"] == "paid"
    assert payments["USB-C docking hub Pro, 11 ports"] == "pending_owner"
    assert any(e["kind"] == "agent.thinking" for e in events)


def test_gemini_loop_protocol():
    fake = FakeGemini(*MANIPULATED_RUN)
    run(fake)
    config = fake.requests[0]["config"]
    assert config.automatic_function_calling.disable  # SpendGuard runs every tool itself
    names = [d.name for d in config.tools[0].function_declarations]
    assert names == ["search_catalog", "get_allowance", "purchase"]

    turn2 = fake.requests[1]["contents"]
    assert turn2[1] is MANIPULATED_RUN[0].candidates[0].content  # echoed unchanged
    replies = [p.function_response for p in turn2[2].parts]
    assert [(r.id, r.name) for r in replies] == [("c1", "get_allowance"), ("c2", "search_catalog")]
    assert "untrusted" in replies[1].response["output"]["note"]


def test_tool_errors_go_back_as_error_responses():
    fake = FakeGemini(
        model_turn(call("c1", "purchase", item_id="nope", quantity=1, reason="x")),
        model_turn(types.Part(text="Could not buy it.")),
    )
    events, _ = run(fake)
    assert [e["is_error"] for e in events if e["kind"] == "tool.result"] == [True]
    reply = fake.requests[1]["contents"][2].parts[0].function_response
    assert "error" in reply.response


def test_safety_stop_rate_limit_and_step_cap():
    blocked, _ = run(FakeGemini(model_turn(types.Part(text=""), finish="SAFETY")))
    assert blocked[-1]["kind"] == "run.error" and "SAFETY" in blocked[-1]["message"]

    limited, _ = run(FakeGemini(errors.ClientError(429, {"error": {"message": "quota", "status": "RESOURCE_EXHAUSTED"}})))
    assert limited[-1]["kind"] == "run.error" and "429" in limited[-1]["message"]

    daily = errors.ClientError(429, {"error": {"message": "Generate content requests per day limit exceeded on Free Tier.",
                                               "status": "RESOURCE_EXHAUSTED"}})
    exhausted, _ = run(FakeGemini(daily))
    assert "daily request limit" in exhausted[-1]["message"] and "AGENT_MODEL" in exhausted[-1]["message"]

    looping, _ = run(FakeGemini(*[model_turn(call(f"c{i}", "get_allowance")) for i in range(20)]))
    assert looping[-1]["kind"] == "run.error" and "12 steps" in looping[-1]["message"]


def test_real_client_options_keep_function_calling_api():
    from app.agent.factory import _gemini_http_options

    options = _gemini_http_options()
    assert options.api_version == "v1beta"  # "v1" answers 400 to function declarations
    assert 503 in options.retry_options.http_status_codes
