import { useState } from 'react'
import { api, money } from '../api.js'

const LABELS = {
  paid: 'Paid',
  pending_owner: 'Needs you',
  denied: 'Denied',
  rejected: 'Rejected',
  failed: 'Failed',
  unknown: 'Unknown',
  refunded: 'Refunded',
}

export function StatusChip({ status }) {
  return <span className={`chip ${status}`}>{LABELS[status] ?? status}</span>
}

export default function PaymentsTable({ payments, onChange, onError }) {
  const [busy, setBusy] = useState(null)

  async function refund(id) {
    setBusy(id)
    try {
      await api.refund(id)
      onChange()
    } catch (error) {
      onError(error.message)
    } finally {
      setBusy(null)
    }
  }

  return (
    <section className="card">
      <h2>Payments <span className="muted small">{payments.length}</span></h2>
      {payments.length === 0 ? (
        <p className="muted small">No payment requests yet. Give the agent a task.</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>Item</th><th>Amount</th><th>Decision</th><th /></tr>
            </thead>
            <tbody>
              {[...payments].reverse().map((p) => (
                <tr key={p.id}>
                  <td>
                    <div>{p.item}</div>
                    <div className="muted small">“{p.reason}”</div>
                    {p.error && <div className="small" style={{ color: 'var(--denied)' }}>{p.error}</div>}
                  </td>
                  <td className="amount">{money(p.amount, p.currency)}</td>
                  <td>
                    <StatusChip status={p.status} />
                    <div>{p.rules.map((r) => <span key={r.rule_id} className="rule" title={r.reason}>{r.rule_id}</span>)}</div>
                  </td>
                  <td>
                    {p.status === 'pending_owner' && p.approve_url && (
                      // Same tab: PayPal redirects back here after approval.
                      <a className="btn small primary" href={p.approve_url}>Approve on PayPal</a>
                    )}
                    {p.status === 'paid' && (
                      <button className="btn small" disabled={busy === p.id} onClick={() => refund(p.id)}>Refund</button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
