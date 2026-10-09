const BASE = '/api/v1'

async function getJSON(path) {
  const res = await fetch(`${BASE}${path}`, { headers: { Accept: 'application/json' } })
  if (!res.ok) throw new Error(`HTTP ${res.status} on ${path}`)
  return res.json()
}

export const fetchLiveStatus = () => getJSON('/live-status')
export const fetchTuningCheck = () => getJSON('/tuning-check')
export const fetchSystemInfo = () => getJSON('/system-info')

export async function countTokens(text, system) {
  const res = await fetch(`${BASE}/count-tokens`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(system ? { text, system } : { text }),
  })
  if (!res.ok) {
    let detail = `HTTP ${res.status} on POST /count-tokens`
    try {
      const body = await res.json()
      if (body?.detail) detail = body.detail
    } catch {
      /* non-JSON error body */
    }
    throw new Error(detail)
  }
  return res.json()
}

export async function postConfig(body) {
  const res = await fetch(`${BASE}/config`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(`HTTP ${res.status} on POST /config`)
  return res.json()
}

export async function resetStats() {
  const res = await fetch(`${BASE}/stats/reset`, {
    method: 'POST',
    headers: { Accept: 'application/json' },
  })
  if (!res.ok) throw new Error(`HTTP ${res.status} on POST /stats/reset`)
  return res.json()
}
