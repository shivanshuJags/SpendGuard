import { useEffect, useRef, useState } from 'react'
import { api } from '../api.js'
import { StatusChip } from './PaymentsTable.jsx'

const DEMO_TASK =
  'Restock the office: we are out of coffee and printer paper, and the meeting room needs a USB-C hub ' +
  'that can drive two 4K screens. Keep the whole thing under $120.'

const TERMINAL = new Set(['run.finished', 'run.error'])

export default function AgentPanel({ allowance, onPaymentEvent }) {
  const [task, setTask] = useState(DEMO_TASK)
  const [events, setEvents] = useState([])
  const [running, setRunning] = useState(false)
  const [error, setError] = useState(null)
  const source = useRef(null)

  // Close the stream if the panel unmounts mid-run.
  useEffect(() => () => source.current?.close(), [])

  async function start() {
    setError(null)
    setEvents([])
    try {
      const run = await api.startRun(allowance.id, task)
      setRunning(true)
      const es = api.runEvents(run.id)
      source.current = es
      es.onmessage = (message) => {
        const event = JSON.parse(message.data)
        setEvents((prev) => [...prev, event])
        if (event.kind === 'tool.result' && event.payment_id) onPaymentEvent()
        if (TERMINAL.has(event.kind)) {
          es.close() // otherwise the browser would reconnect
          setRunning(false)
          onPaymentEvent()
        }
      }
      es.onerror = () => {
        // EventSource retries by itself; only give up once it has closed.
        if (es.readyState === EventSource.CLOSED) setRunning(false)
      }
    } catch (e) {
      setError(e.message)
    }
  }

  return (
    <section className="card">
      <h2>
        Shopping agent
        <span className="muted small">Claude proposes · SpendGuard decides · PayPal pays</span>
      </h2>
      <textarea className="task" value={task} onChange={(e) => setTask(e.target.value)} disabled={running} />
      <div className="row" style={{ marginTop: 8, justifyContent: 'space-between' }}>
        <span className="muted small">
          {allowance ? (allowance.frozen ? 'Allowance is frozen: every purchase will be denied.' : '') : 'Create an allowance first.'}
        </span>
        <button className="btn primary" onClick={start} disabled={!allowance || running || !task.trim()}>
          {running ? 'Agent working…' : 'Run agent'}
        </button>
      </div>
      {error && <div className="toast error" style={{ marginTop: 10 }}>{error}</div>}

      <ol className="timeline">
        {events.map((e) => <Event key={e.seq} event={e} />)}
      </ol>
    </section>
  )
}

function Event({ event: e }) {
  const step = e.step ? <span className="step">step {e.step}</span> : null
  switch (e.kind) {
    case 'run.started':
      return <li className="event"><div className="head">Agent started <span className="step">{e.model}</span></div></li>
    case 'agent.thinking':
      return <li className="event thinking"><div className="head">Thinking {step}</div>{e.text}</li>
    case 'agent.text':
      return <li className="event text"><div className="head">Claude {step}</div>{e.text}</li>
    case 'tool.call':
      return <li className="event"><div className="head">→ <code>{e.tool}</code> {step}</div>{describeCall(e)}</li>
    case 'tool.result':
      return <ToolResult event={e} step={step} />
    case 'run.finished':
      return <li className="event paid"><div className="head">Done in {e.steps} steps</div></li>
    case 'run.error':
      return <li className="event error"><div className="head">Stopped {step}</div>{e.message}</li>
    default:
      return null
  }
}

function describeCall({ tool, input }) {
  if (tool === 'purchase') return <>{input.quantity} × <code>{input.item_id}</code> — “{input.reason}”</>
  if (tool === 'search_catalog') return <>query: “{input.query || 'everything'}”</>
  return <span className="muted">checking the owner's rules</span>
}

function ToolResult({ event: e, step }) {
  const r = e.result
  if (e.is_error) {
    return <li className="event error"><div className="head">Tool error {step}</div>{String(r)}</li>
  }
  if (e.tool === 'purchase') {
    return (
      <li className={`event ${r.status}`}>
        <div className="head"><StatusChip status={r.status} /> SpendGuard policy {step}</div>
        {r.message}
      </li>
    )
  }
  if (e.tool === 'search_catalog') {
    return <li className="event"><div className="head">← {r.items.length} items found {step}</div></li>
  }
  return <li className="event"><div className="head">← {r.remaining} remaining of {r.budget} {step}</div></li>
}
