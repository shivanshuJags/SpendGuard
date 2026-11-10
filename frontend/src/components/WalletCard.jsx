import { useState } from 'react'
import { api } from '../api.js'

export default function WalletCard({ wallet, onChange, onError }) {
  const [busy, setBusy] = useState(false)

  async function link() {
    setBusy(true)
    try {
      const { approve_url } = await api.linkWallet()
      // Full-page redirect: PayPal sends the owner back to /api/wallet/return.
      window.location.assign(approve_url)
    } catch (error) {
      onError(error.message)
      setBusy(false)
    }
  }

  async function unlink() {
    setBusy(true)
    try {
      await api.unlinkWallet()
      onChange()
    } catch (error) {
      onError(error.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="card">
      <h2>
        PayPal wallet
        {wallet?.linked ? <span className="chip paid">Linked</span> : <span className="chip refunded">Not linked</span>}
      </h2>
      {wallet?.linked ? (
        <div className="row" style={{ justifyContent: 'space-between' }}>
          <div>
            <div>{wallet.payer_email}</div>
            <div className="muted small">Small purchases are paid automatically with this saved wallet.</div>
          </div>
          <button className="btn small danger" onClick={unlink} disabled={busy}>Unlink</button>
        </div>
      ) : (
        <div className="row" style={{ justifyContent: 'space-between' }}>
          <div className="muted small">
            Link once with PayPal Vault. Until then, every purchase needs your approval on PayPal.
          </div>
          <button className="btn primary" onClick={link} disabled={busy}>Link PayPal</button>
        </div>
      )}
    </section>
  )
}
