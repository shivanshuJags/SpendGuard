// Thin client for the SpendGuard API. Every call returns parsed JSON or throws
// an Error carrying the server's message.

async function request(method, path, body) {
  const response = await fetch(`/api${path}`, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
  const data = response.status === 204 ? null : await response.json().catch(() => null)
  if (!response.ok) {
    const message = data?.message || data?.detail?.[0]?.msg || `HTTP ${response.status}`
    throw new Error(message)
  }
  return data
}

export const api = {
  wallet: () => request('GET', '/wallet'),
  linkWallet: () => request('POST', '/wallet/link'),
  unlinkWallet: () => request('DELETE', '/wallet'),

  allowances: () => request('GET', '/allowances'),
  createDemoAllowance: () => request('POST', '/demo/allowance'),
  freeze: (id, frozen) => request('POST', `/allowances/${id}/freeze`, { frozen }),
  payments: (id) => request('GET', `/allowances/${id}/payments`),
  refund: (paymentId) => request('POST', `/payments/${paymentId}/refund`),

  audit: () => request('GET', '/audit?limit=500'),

  startRun: (allowanceId, task) => request('POST', '/agent/runs', { allowance_id: allowanceId, task }),
  // Server-Sent Events stream of one run's events.
  runEvents: (runId) => new EventSource(`/api/agent/runs/${runId}/events`),
}

// Amounts arrive as decimal strings ("12.50"); this is display formatting only.
export function money(amount, currency = 'USD') {
  return new Intl.NumberFormat('en-US', { style: 'currency', currency }).format(Number(amount))
}
