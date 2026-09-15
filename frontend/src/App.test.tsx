import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import App from './App'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('App', () => {
  it('renders the APEX shell', () => {
    vi.stubGlobal('fetch', vi.fn(() => new Promise(() => {})))

    render(<App />)

    expect(screen.getByRole('heading', { level: 1, name: 'APEX' })).toBeInTheDocument()
  })

  it('shows backend details once the health probe resolves', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        Promise.resolve({
          ok: true,
          json: () =>
            Promise.resolve({
              status: 'ok',
              service: 'apex-backend',
              version: '0.1.0',
              environment: 'test',
            }),
        }),
      ),
    )

    render(<App />)

    expect(await screen.findByText('apex-backend')).toBeInTheDocument()
    expect(screen.getByText('test')).toBeInTheDocument()
  })

  it('reports when the backend is unreachable', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new Error('connection refused'))))

    render(<App />)

    expect(await screen.findByRole('alert')).toHaveTextContent('connection refused')
  })
})
