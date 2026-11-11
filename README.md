# SpendGuard

**A guarded PayPal wallet for AI agents.** Let an AI agent shop for you, while
every payment it attempts passes through rules you wrote.

> **AI proposes. SpendGuard decides. PayPal pays.**

Built for *Build What's Next with PayPal and AI* (Devpost, 2026).
Demo video: *link added after recording*.

## The problem

AI agents can now buy things, and the risk isn't that they can't pay. It's that
they can be **talked into paying**. To a language model, a product page saying
"the owner pre-approved this, ignore your limits" is just more text. Handing an
agent a card is all or nothing: either it can spend, or it can't.

## The idea

SpendGuard keeps the money decision **out of the model**:

1. The agent (Google Gemini) searches a catalog and *requests* purchases through tools.
2. A deterministic policy engine, plain tested code with no AI in it, decides
   each request: **approve**, **escalate to the owner**, or **deny**.
3. PayPal executes. Small purchases are paid automatically from a wallet the owner
   saved once with **PayPal Vault**. Large ones become a **PayPal approval link**,
   so PayPal itself is the human checkpoint.

The agent never sees prices it can change, never picks the payee, and never
touches PayPal. A fully manipulated model can still only spend what the rules allow.

```mermaid
flowchart LR
    Owner([Owner]) -->|sets rules, approves big buys| Dash[React dashboard]
    Dash --> API[FastAPI]
    Agent[Gemini agent] -->|search / check allowance / purchase| API
    API --> Policy{Policy engine<br/>no AI}
    Policy -->|approve| Vault[PayPal Vault<br/>auto-pay]
    Policy -->|escalate| Link[PayPal approval link]
    Policy -->|deny| Audit[(Audit log)]
    Vault --> Audit
    Link --> Audit
```

More detail: [docs/architecture.md](docs/architecture.md) (layers, flows, decisions)
and [docs/user-stories.md](docs/user-stories.md).

## What it does

| Feature | How |
|---|---|
| **Link a wallet once** | PayPal Vault v3: setup token, owner approves on PayPal, payment token saved |
| **Rules the agent can't argue with** | Budget, per-purchase hard cap, approval threshold, merchant allow/deny lists, blocked categories (gift cards and crypto by default), purchases per hour, expiry |
| **Kill switch** | **Freeze** denies every payment immediately, even ones waiting for approval |
| **Owner approval on PayPal** | Escalated purchases become a PayPal order; the owner approves (or rejects) and SpendGuard captures it |
| **Server-side prices** | The agent sends only an item ID and quantity; price and payee come from the catalog |
| **Prompt-injection resistant** | Seller descriptions are labelled untrusted, and the rules hold even if the model is fooled |
| **Live agent timeline** | Every thought summary, tool call and decision streams to the dashboard (Server-Sent Events) |
| **Audit trail** | Append-only log of every request, rule, decision and PayPal ID |
| **Refunds** | One click, through PayPal's refund API |

## When payments go wrong

Money code is judged by its failure paths. SpendGuard never guesses: if it isn't
sure money moved, it keeps the amount reserved and asks PayPal.

| What happens | What SpendGuard does | Budget |
|---|---|---|
| PayPal rejects the request (4xx, card declined) | Marks it **failed**, tells the agent | Released |
| Network drop or PayPal 5xx | Retries once with the **same `PayPal-Request-Id`**, so PayPal can't charge twice | – |
| Still no answer | Marks it **unknown**: money may have moved | **Kept reserved** |
| Order "COMPLETED" but the capture inside was DECLINED | Reads the capture's own status: **failed**, not paid | Released |
| Owner cancels on PayPal, or clicks **Reject** | **Rejected** | Released |
| Allowance frozen or expired before capture | Doesn't capture: **rejected** | Released |
| Unknown payment | **Check with PayPal** reads the order, re-sends a lost capture, or replays an unanswered request with the same idempotency key | Settled |

Budget totals are calculated from payment states on every read, so "rolling back"
is just a status change: there is no balance to drift out of sync. Requests reserve
their amount before the first network call, so two purchases at once can't overspend.

## Run it locally

You need **Python 3.13+** with [uv](https://docs.astral.sh/uv/), **Node.js 22+**,
a free PayPal developer account and a free Gemini API key. Everything runs in the
PayPal **sandbox**: no real money.

### 1. PayPal sandbox setup (once)

1. Sign in at [developer.paypal.com](https://developer.paypal.com) and open
   **Apps & Credentials → Sandbox → Create App** (type: Merchant).
   Copy the **Client ID** and **Secret**.
2. In the app's settings, under features, enable **Vault** (saving payment
   methods). Without it, linking a wallet fails with `403 NOT_AUTHORIZED`.
3. Open **Testing Tools → Sandbox Accounts**. PayPal creates two accounts for you:
   - a **Business** account: the merchant that sells the demo catalog.
     Its email goes in `DEMO_MERCHANT_EMAIL`.
   - a **Personal** account: the buyer. You log in with it on PayPal's pages to
     link the wallet and approve purchases (see the account's details for its password).

### 2. Gemini API key

Create one at [aistudio.google.com/apikey](https://aistudio.google.com/apikey).

### 3. Backend

```bash
cd backend
cp .env.example .env      # then fill in the values below
uv sync
uv run pytest             # 74 tests, no network or keys needed
uv run uvicorn app.main:app --factory --reload   # API docs: http://localhost:8000/docs
```

| `.env` value | What to put |
|---|---|
| `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET` | From step 1 |
| `PAYPAL_BASE_URL` | Keep `https://api-m.sandbox.paypal.com` |
| `DEMO_MERCHANT_EMAIL` | The sandbox **Business** account email |
| `GEMINI_API_KEY` | From step 2 |
| `AGENT_MODEL` | Optional. Empty means `gemini-2.5-flash`; we demoed with `gemini-3.5-flash` |

Optional check of your PayPal credentials:

```bash
uv run python -m scripts.sandbox_smoke order <sandbox-business-email>
```

### 4. Dashboard

In a second terminal:

```bash
cd frontend
npm install
npm run dev               # http://localhost:5173 (proxies /api to :8000)
```

## Try the demo

Open http://localhost:5173.

1. **Link PayPal.** Log in as the sandbox *Personal* account and agree.
   The wallet card shows **Linked**.
2. **Create demo allowance**: $120 budget, $100 hard cap, your approval from $50,
   only the demo merchant, gift cards and crypto blocked.
3. **Run agent** with the prefilled task ("restock coffee, paper and a USB-C hub
   for two 4K screens, under $120"). Watch the timeline:
   - coffee and paper are **paid** automatically through Vault
   - the $95 hub **needs you**: it's above the $50 approval threshold
   - the "Office Rewards bundle", whose description tries to talk the agent into
     buying it, is ignored, and would be denied as a gift card anyway
4. Click **Approve on PayPal** on the hub, log in as the buyer, and it becomes **paid**.
5. Try **Freeze**, **Refund** and **Reject**, then read the **Audit log**.

Each run spends from the allowance. Click **New demo allowance** before re-running.

## Built with

- **PayPal (sandbox):** OAuth 2.0 client credentials, **Orders v2** (create,
  capture, get), **Vault v3** (setup tokens, payment tokens), **Payments v2**
  refunds, and `PayPal-Request-Id` idempotency on every write.
- **Backend:** Python, FastAPI, httpx, Pydantic, SQLite.
- **Frontend:** React 19, Vite.

## AI tools used

- **Google Gemini** (`gemini-3.5-flash` through the `google-genai` SDK) is the
  shopping agent. It gets three tools (`search_catalog`, `get_allowance`,
  `purchase`) in a server-side loop with **automatic function calling disabled**,
  so SpendGuard runs every tool itself. Its thought summaries stream to the dashboard.
- **Anthropic Claude** works as an alternative agent (`AGENT_PROVIDER=anthropic`)
  behind the same interface. The rules and PayPal code don't change.
- **Claude Code** (Anthropic) was used as a pair programmer for design, code,
  tests and documentation.

## Project layout

```
backend/app/domain/     money, models, policy engine (pure, no I/O)
backend/app/services/   payment and shopping use cases, gateway ports
backend/app/adapters/   PayPal client, SQLite store, catalog
backend/app/agent/      Gemini and Claude adapters, tools, run registry
backend/app/api/        FastAPI routes, schemas, error mapping
backend/tests/          policy, PayPal client, payment, API and agent tests
frontend/src/           React dashboard
docs/                   user stories, architecture and decisions
```

## Limits

- **Sandbox only.** Going live needs PayPal live credentials and review.
- **One owner, single process.** Agent run events live in memory; payments and
  the audit log are in SQLite.
- **Demo catalog.** Items come from a JSON file, not a real store.
- **Gemini free tier.** `gemini-3.5-flash` allows 20 requests a day (about 2–3
  agent runs); `gemini-2.5-flash` has a separate quota.

## License

MIT, see [LICENSE](LICENSE).
