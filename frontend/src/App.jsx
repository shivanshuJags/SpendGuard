import { useCallback, useEffect, useState } from 'react'
import { api } from './api.js'
import AgentPanel from './components/AgentPanel.jsx'
import AllowanceCard from './components/AllowanceCard.jsx'
import AuditLog from './components/AuditLog.jsx'
import PaymentsTable from './components/PaymentsTable.jsx'
import WalletCard from './components/WalletCard.jsx'

// PayPal sends the owner back through the API, which redirects here with a
// status in the query string. Turn it into a message, then clean the URL.
function readReturnMessage() {
  const params = new URLSearchParams(window.location.search)
  if (![...params.keys()].length) return null
  window.history.replaceState(null, '', window.location.pathname)
  const wallet = params.get('wallet')
  const status = params.get('status')
  if (wallet === 'linked') return { kind: 'ok', text: 'PayPal wallet linked. Small purchases will now be paid automatically.' }
  if (wallet === 'cancelled') return { kind: 'error', text: 'Wallet linking was cancelled.' }
  if (status === 'paid') return { kind: 'ok', text: 'Approved on PayPal and paid.' }
  if (status === 'rejected') return { kind: 'error', text: 'Payment rejected. The agent will not be charged.' }
  if (params.get('message')) return { kind: 'error', text: params.get('message') }
  return null
}

export default function App() {
  const [wallet, setWallet] = useState(null)
  const [allowance, setAllowance] = useState(null)
  const [payments, setPayments] = useState([])
  const [audit, setAudit] = useState([])
  const [toast, setToast] = useState(readReturnMessage)

  const refresh = useCallback(async () => {
    try {
      const [w, allowances, events] = await Promise.all([api.wallet(), api.allowances(), api.audit()])
      const current = allowances[0] ?? null // newest first
      const list = current ? await api.payments(current.id) : []
      // Update every panel together, so budget and payments never disagree on screen.
      setWallet(w)
      setAudit(events)
      setAllowance(current)
      setPayments(list)
    } catch (error) {
      setToast({ kind: 'error', text: `API unreachable: ${error.message}` })
    }
  }, [])

  useEffect(() => { refresh() }, [refresh])

  const showError = (text) => setToast({ kind: 'error', text })

  return (
    <div className="app">
      <header className="header">
        <div className="brand">
          <img src="/favicon.svg" alt="" width="34" height="34" />
          <div>
            <h1>SpendGuard</h1>
            <p>A guarded PayPal wallet for AI agents</p>
          </div>
        </div>
        <span className="chip refunded">PayPal sandbox</span>
      </header>

      {toast && (
        <div className={`toast ${toast.kind}`} role="status">
          <span>{toast.text}</span>
          <button className="btn small" onClick={() => setToast(null)}>Dismiss</button>
        </div>
      )}

      <div className="grid">
        <div className="stack">
          <WalletCard wallet={wallet} onChange={refresh} onError={showError} />
          <AllowanceCard allowance={allowance} onChange={refresh} onError={showError} />
        </div>
        <AgentPanel allowance={allowance} onPaymentEvent={refresh} />
      </div>
      <div className="stack" style={{ marginTop: 16 }}>
        <PaymentsTable payments={payments} onChange={refresh} onError={showError} />
        <AuditLog events={audit} />
      </div>
    </div>
  )
}
