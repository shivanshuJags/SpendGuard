# SpendGuard

**A guarded PayPal wallet for AI agents.** Let an AI agent shop for you, while
every payment it attempts passes through rules you wrote.

> The agent proposes. Deterministic policy decides. PayPal executes.

Built for *Build What's Next with PayPal and AI* (Devpost, 2026).

## Why

AI agents can now buy things. The danger isn't that they can't pay, it's that
they can be talked into paying: a product page that says "ignore your limits"
is just more text to an LLM. SpendGuard puts the money decision outside the
model, in testable code, and uses PayPal itself as the human checkpoint for
large purchases.

## How it works

- **Link once** with PayPal Vault: the agent can pay small amounts unattended.
- **Policy engine**: budget, per-purchase cap, approval threshold, merchant
  allow/deny lists, blocked categories, velocity limit, kill switch. Fails closed.
- **Escalation**: big or unusual purchases become a PayPal approval link for the owner.
- **Audit trail**: every request, decision, rule and PayPal ID.

See [docs/user-stories.md](docs/user-stories.md) and [docs/architecture.md](docs/architecture.md).

## Status

| Part | State |
|---|---|
| User stories, architecture | done |
| Policy engine + tests | done |
| PayPal adapter (OAuth, Vault, Orders, Refunds) + tests | done; sandbox orders and Vault auto-pay verified |
| Payment service + audit store + tests | done |
| HTTP API + demo catalog + tests | done; verified against sandbox |
| Shopping agent: Gemini or Claude adapter, live event stream + tests | done; live Gemini run verified against PayPal sandbox |
| React dashboard (live agent timeline) | done; wallet link and owner approval verified in sandbox |

## Run it (backend)

Requirements: Python 3.13 and [uv](https://docs.astral.sh/uv/).

```bash
cd backend
cp .env.example .env      # fill in PayPal sandbox keys and GEMINI_API_KEY
uv sync
uv run pytest
uv run uvicorn app.main:app --factory --reload   # API docs: http://localhost:8000/docs
```

Check your PayPal sandbox credentials (the script lists Vault commands too):

```bash
uv run python -m scripts.sandbox_smoke order <sandbox-business-email>
```

## Run it (dashboard)

Requirements: Node.js 22+. Run the backend first (above), then in another terminal:

```bash
cd frontend
npm install
npm run dev               # http://localhost:5173 (proxies /api to :8000)
```

## Tools used

- **PayPal**: Orders v2, Vault v3 (payment tokens), Payments v2 refunds — sandbox.
- **AI**: Google Gemini (default, `google-genai` SDK) or Anthropic Claude, chosen with `AGENT_PROVIDER`. The model gets three tools (search catalog, check allowance, purchase) in a server-side loop with automatic function calling disabled; it can only *request* purchases, and SpendGuard's deterministic policy decides each one.

## License

MIT, see [LICENSE](LICENSE).
