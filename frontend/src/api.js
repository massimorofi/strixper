const BASE = '/api/v1'

async function getJSON(path) {
  const res = await fetch(`${BASE}${path}`, { headers: { Accept: 'application/json' } })
  if (!res.ok) throw new Error(`HTTP ${res.status} on ${path}`)
  return res.json()
}

export const fetchLiveStatus = () => getJSON('/live-status')
export const fetchTuningCheck = () => getJSON('/tuning-check')
export const fetchSystemInfo = () => getJSON('/system-info')

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
