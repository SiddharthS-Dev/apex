/**
 * Thin client for the APEX backend.
 *
 * The base URL comes from the environment so the same build can target local,
 * staging and production deployments.
 */

const API_BASE_URL =
  import.meta.env.VITE_APEX_API_BASE_URL ?? 'http://localhost:8000/api/v1'

/** Root of the backend service, with the versioned API path stripped off. */
const SERVICE_ROOT = API_BASE_URL.replace(/\/api\/v\d+\/?$/, '')

export interface Health {
  status: string
  service: string
  version: string
  environment: string
}

/** Probe the backend liveness endpoint. */
export async function fetchHealth(): Promise<Health> {
  const response = await fetch(`${SERVICE_ROOT}/health`)
  if (!response.ok) {
    throw new Error(`Health check failed with status ${response.status}`)
  }
  return (await response.json()) as Health
}
