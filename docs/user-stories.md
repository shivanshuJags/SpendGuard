# SpendGuard — User Stories

SpendGuard is a guarded PayPal wallet for AI agents. A human **owner** lets an
AI **agent** shop on their behalf, but every payment the agent attempts passes
through rules the owner wrote. The agent proposes; deterministic policy decides.

## Actors

| Actor | Who | What they care about |
|---|---|---|
| **Owner** | The human whose PayPal money is spent | Control, visibility, no surprises |
| **Agent** | An LLM (Claude) with tools, doing a task for the owner | Finishing the task within its allowance |
| **Merchant** | A seller (PayPal sandbox business account) | Getting paid |
| **Judge / viewer** | Hackathon judge running the demo | Seeing it work in under 3 minutes |

## Priorities

- **Must** — the demo does not work without it. Build these first.
- **Should** — makes the project stand out. Build after all Musts pass.
- **Could** — only if time remains.

---

## Epic 1 — Connect a wallet

### US-1.1 (Must) Link PayPal once
**As an** owner, **I want** to link my PayPal account to SpendGuard once,
**so that** my agent can pay later without me logging in every time.

Acceptance criteria
- Owner clicks "Link PayPal", approves in PayPal's sandbox, and returns to SpendGuard.
- SpendGuard stores a PayPal **vault payment token** (never a password or card number).
- The dashboard shows the wallet as "Linked" with the PayPal account email.
- Owner can unlink, which deletes the token at PayPal and locally.

### US-1.2 (Must) Give the agent an allowance
**As an** owner, **I want** to create an agent "allowance" with a budget,
**so that** the agent cannot spend more than I decide.

Acceptance criteria
- Owner sets: total budget, per-purchase limit, currency, and an expiry date.
- Remaining budget is shown and decreases after each captured payment.

---

## Epic 2 — Policy (the guardrails)

### US-2.1 (Must) Per-purchase and total limits
**As an** owner, **I want** purchases above my per-purchase limit or beyond the
remaining budget to be blocked, **so that** a confused agent cannot overspend.

Acceptance criteria
- A request over the remaining budget is **denied** with a reason.
- A request over the per-purchase limit (a hard cap) is **denied** with a reason.
- Amounts waiting for my approval are reserved, so two pending requests cannot
  together exceed the budget.
- The agent receives the reason so it can adapt (US-3.3).

### US-2.2 (Must) Approval threshold (human in the loop)
**As an** owner, **I want** purchases above an amount I choose to wait for my
approval, **so that** small things are automatic and big things are mine to decide.

Acceptance criteria
- Below the threshold: paid automatically using the vault token.
- At or above: the request appears in my approval queue with the agent's reasoning,
  and I approve it on PayPal's own approval page (PayPal is the final checkpoint).
- If I reject it, the agent is told "rejected by owner" and continues without it.

### US-2.3 (Must) Merchant allow/deny list
**As an** owner, **I want** to restrict which merchants the agent may pay,
**so that** it cannot send money to unknown sellers.

Acceptance criteria
- Payee not on the allowlist → denied (or escalated, configurable).
- Payee on the denylist → always denied.

### US-2.4 (Should) Category rules
**As an** owner, **I want** to block categories (e.g. gift cards, crypto),
**so that** common fraud routes are closed.

### US-2.5 (Should) Velocity limit
**As an** owner, **I want** to cap the number of purchases per hour,
**so that** a looping agent cannot drain my budget with many small payments.

### US-2.6 (Must) Prompt-injection resistance
**As an** owner, **I want** the policy to hold even if a product page tells the
agent to "ignore your limits", **so that** text the agent reads cannot change my rules.

Acceptance criteria
- The demo catalog contains a malicious listing with injected instructions.
- The agent may *try* to buy it; the policy engine still denies or escalates it.
- The audit log shows the attempt and the rule that stopped it.

---

## Epic 3 — The agent

### US-3.1 (Must) Give the agent a task in plain language
**As an** owner, **I want** to type a task like "Restock the office: coffee,
printer paper, and a USB-C hub, keep it under $120", **so that** I don't have to
shop myself.

Acceptance criteria
- The agent searches the catalog, compares options, and proposes purchases.
- Every payment attempt goes through the `request_payment` tool → policy engine.
- The agent's steps stream live to the dashboard.

### US-3.2 (Must) The agent explains every purchase
**As an** owner, **I want** each payment request to include the agent's reason,
**so that** I can judge escalations quickly.

### US-3.3 (Should) The agent adapts to decisions
**As an** owner, **I want** the agent to pick a cheaper option when a purchase
is denied, **so that** the task still finishes inside my rules.

---

## Epic 4 — Audit and undo

### US-4.1 (Must) Audit trail
**As an** owner, **I want** a timeline of every request, decision, rule and
PayPal ID, **so that** I can see exactly what happened and why.

Acceptance criteria
- Each entry: time, item, amount, merchant, agent reason, decision, rule IDs,
  PayPal order ID / capture ID.

### US-4.2 (Should) One-click refund
**As an** owner, **I want** to refund a purchase from the audit trail,
**so that** mistakes are easy to undo.

### US-4.3 (Could) Plain-language spending summary
**As an** owner, **I want** an AI summary of the week's agent spending,
**so that** I don't have to read every log line.

### US-4.4 (Could) Kill switch
**As an** owner, **I want** to freeze the allowance instantly,
**so that** all further payments are denied.

---

## Demo script (maps to the Musts)

1. Link PayPal sandbox wallet (US-1.1), create a $120 allowance (US-1.2).
2. Give the office-restock task (US-3.1). Watch it buy coffee automatically (US-2.2).
3. It tries a $95 premium hub → escalated → owner approves on PayPal (US-2.2, US-3.2).
4. It hits the malicious listing → denied, shown in the audit trail (US-2.6, US-4.1).
5. Refund one item (US-4.2).
