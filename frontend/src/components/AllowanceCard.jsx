import { useState } from 'react'
import { api, money } from '../api.js'

export default function AllowanceCard({ allowance, onChange, onError }) {
  const [busy, setBusy] = useState(false)

  async function act(fn) {
    setBusy(true)
    try {
      await fn()
      onChange()
    } catch (error) {
      onError(error.message)
    } finally {
      setBusy(false)
    }
  }

  if (!allowance) {
    return (
      <section className="card">
        <h2>Agent allowance</h2>
        <p className="muted small">
          Demo rules: $120 budget, $100 per-purchase cap, your approval from $50, only the demo
          merchant, gift cards and crypto blocked.
        </p>
        <button className="btn primary" disabled={busy} onClick={() => act(api.createDemoAllowance)}>
          Create demo allowance
        </button>
      </section>
    )
  }

  const c = allowance.currency
  const pct = (v) => `${Math.min(100, (Number(v) / Number(allowance.budget)) * 100)}%`

  return (
    <section className="card">
      <h2>
        Agent allowance
        <label className="switch" title="Kill switch: deny every payment">
          <input
            type="checkbox"
            checked={allowance.frozen}
            disabled={busy}
            onChange={(e) => act(() => api.freeze(allowance.id, e.target.checked))}
          />
          Freeze
        </label>
      </h2>

      <div className="budget-figures">
        <Figure label="Budget" value={money(allowance.budget, c)} />
        <Figure label="Spent" value={money(allowance.spent, c)} />
        <Figure label="Awaiting you" value={money(allowance.reserved, c)} />
        <Figure label="Remaining" value={money(allowance.remaining, c)} />
      </div>
      <div className="bar" aria-label="Budget used">
        <div className="spent" style={{ width: pct(allowance.spent) }} />
        <div className="reserved" style={{ width: pct(allowance.reserved) }} />
      </div>

      <dl className="rules">
        <dt>Auto-pay below</dt>
        <dd>{money(allowance.approval_threshold, c)} (at or above: you approve on PayPal)</dd>
        <dt>Hard cap</dt>
        <dd>{money(allowance.per_purchase_limit, c)} per purchase</dd>
        <dt>Merchants</dt>
        <dd>{allowance.merchant_allowlist ? allowance.merchant_allowlist.join(', ') : 'any'}</dd>
        <dt>Blocked</dt>
        <dd>{allowance.blocked_categories.join(', ') || 'none'}</dd>
        <dt>Velocity</dt>
        <dd>{allowance.max_purchases_per_hour ?? 'no'} purchases / hour</dd>
      </dl>

      {/* Each run spends from this allowance; re-running the demo needs a fresh one. */}
      <div className="row" style={{ marginTop: 12, justifyContent: 'flex-end' }}>
        <button className="btn" disabled={busy} onClick={() => act(api.createDemoAllowance)}>
          New demo allowance
        </button>
      </div>
    </section>
  )
}

function Figure({ label, value }) {
  return (
    <div className="figure">
      <div className="label">{label}</div>
      <div className="value">{value}</div>
    </div>
  )
}
