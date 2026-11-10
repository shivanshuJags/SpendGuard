export default function AuditLog({ events }) {
  return (
    <section className="card">
      <details>
        <summary>Audit log <span className="muted small">({events.length} events, append-only)</span></summary>
        <ul className="audit">
          {[...events].reverse().map((e) => (
            <li key={e.seq}>
              <span className="muted">{new Date(e.at).toLocaleTimeString()}</span>
              <span><code>{e.kind}</code></span>
              <span>{summarize(e)}</span>
            </li>
          ))}
        </ul>
      </details>
    </section>
  )
}

function summarize({ data }) {
  if (data.item) return `${data.item} · ${data.amount} · ${data.outcome}${data.rules?.length ? ` (${data.rules.join(', ')})` : ''}`
  if (data.capture_id) return `capture ${data.capture_id}`
  if (data.error) return data.error
  if (data.task) return data.task
  if (data.message) return data.message
  return Object.keys(data).length ? JSON.stringify(data) : ''
}
