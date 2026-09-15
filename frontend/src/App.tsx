import { useEffect, useState } from 'react'

import { fetchHealth, type Health } from './api'

type Status =
  | { state: 'checking' }
  | { state: 'online'; health: Health }
  | { state: 'offline'; message: string }

export default function App() {
  const [status, setStatus] = useState<Status>({ state: 'checking' })

  useEffect(() => {
    let cancelled = false

    fetchHealth()
      .then((health) => {
        if (!cancelled) setStatus({ state: 'online', health })
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          const message = error instanceof Error ? error.message : 'Unknown error'
          setStatus({ state: 'offline', message })
        }
      })

    return () => {
      cancelled = true
    }
  }, [])

  return (
    <main className="shell">
      <header className="shell__header">
        <h1>APEX</h1>
        <p className="shell__tagline">
          Enterprise knowledge, governance and intelligence platform
        </p>
      </header>

      <section className="panel" aria-labelledby="backend-status">
        <h2 id="backend-status">Backend status</h2>
        {status.state === 'checking' && <p role="status">Checking…</p>}
        {status.state === 'online' && (
          <dl className="facts">
            <dt>Status</dt>
            <dd>{status.health.status}</dd>
            <dt>Service</dt>
            <dd>{status.health.service}</dd>
            <dt>Version</dt>
            <dd>{status.health.version}</dd>
            <dt>Environment</dt>
            <dd>{status.health.environment}</dd>
          </dl>
        )}
        {status.state === 'offline' && (
          <p role="alert" className="error">
            Backend unreachable — {status.message}
          </p>
        )}
      </section>
    </main>
  )
}
