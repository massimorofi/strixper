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

// -- LLM-Runner: docker run configurations -----------------------------------

export const fetchRunnerConfigs = () => getJSON('/runner/configs')
export const fetchRunnerConfig = (id) => getJSON(`/runner/configs/${id}`)

async function sendJSON(method, path, body) {
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    let detail = `HTTP ${res.status} on ${method} ${path}`
    try {
      const j = await res.json()
      if (j?.detail) detail = j.detail
    } catch {
      /* non-JSON error body */
    }
    throw new Error(detail)
  }
  return res.json()
}

export const createRunnerConfig = (cfg) => sendJSON('POST', '/runner/configs', cfg)
export const updateRunnerConfig = (id, cfg) =>
  sendJSON('PUT', `/runner/configs/${id}`, cfg)
export const deleteRunnerConfig = (id) =>
  fetch(`${BASE}/runner/configs/${id}`, { method: 'DELETE' }).then(async (res) => {
    if (!res.ok) {
      let detail = `HTTP ${res.status} on DELETE /runner/configs/${id}`
      try {
        const j = await res.json()
        if (j?.detail) detail = j.detail
      } catch {
        /* non-JSON error body */
      }
      throw new Error(detail)
    }
    return res.json()
  })
export const stopRunnerRun = (runId) =>
  sendJSON('POST', `/runner/runs/${runId}/stop`, {})

// Stream a docker run from the backend. onStdout(textChunk), onStarted(info),
// onExit({exit_code}), onError(message).
export async function streamRunnerRun(
  configId,
  { onStdout, onStarted, onExit, onError, signal } = {},
) {
  let res
  try {
    res = await fetch(`${BASE}/runner/configs/${configId}/run`, {
      method: 'POST',
      headers: { Accept: 'text/event-stream' },
      signal,
    })
  } catch (err) {
    onError?.(err?.message || 'Request failed')
    return
  }

  if (!res.ok || !res.body) {
    let detail = `HTTP ${res.status} on POST /runner/configs/${configId}/run`
    try {
      const j = await res.json()
      if (j?.detail) detail = j.detail
    } catch {
      /* non-JSON error body */
    }
    onError?.(detail)
    return
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let pending = ''

  const handleFrame = (frame) => {
    let event = 'message'
    const dataLines = []
    for (const raw of frame.split('\n')) {
      const line = raw.replace(/\r$/, '')
      if (line.startsWith('event:')) event = line.slice(6).trim()
      else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim())
    }
    if (dataLines.length === 0) return
    let data
    try {
      data = JSON.parse(dataLines.join('\n'))
    } catch {
      return
    }
    if (event === 'started') onStarted?.(data)
    else if (event === 'stdout') onStdout?.(data.text || '')
    else if (event === 'exit') onExit?.(data)
    else if (event === 'error') onError?.(data.message || 'Stream error')
  }

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      pending += decoder.decode(value, { stream: true })
      let idx
      while ((idx = pending.indexOf('\n\n')) !== -1) {
        const frame = pending.slice(0, idx)
        pending = pending.slice(idx + 2)
        if (frame.trim()) handleFrame(frame)
      }
    }
    if (pending.trim()) handleFrame(pending)
  } catch (err) {
    onError?.(err?.message || 'Stream interrupted')
  }
}

// Incremental splitter for the raw-text completions style, where the model
// embeds its chain-of-thought inline between <think> ... </think> markers.
const THINK_OPEN = '<think>'
const THINK_CLOSE = '</think>'

export function createThinkSplitter() {
  let buffer = ''
  let inThink = false
  // Feed raw text chunks; returns { reasoning, content } for this step.
  return (chunk) => {
    buffer += chunk
    let reasoning = ''
    let content = ''
    while (buffer.length > 0) {
      if (inThink) {
        const end = buffer.indexOf(THINK_CLOSE)
        if (end === -1) {
          reasoning += buffer
          buffer = ''
          break
        }
        reasoning += buffer.slice(0, end)
        buffer = buffer.slice(end + THINK_CLOSE.length)
        inThink = false
      } else {
        const start = buffer.indexOf(THINK_OPEN)
        if (start === -1) {
          content += buffer
          buffer = ''
          break
        }
        content += buffer.slice(0, start)
        buffer = buffer.slice(start + THINK_OPEN.length)
        inThink = true
      }
    }
    return { reasoning, content }
  }
}

// Stream a chat turn from the backend /api/v1/chat proxy.
// onReasoning(textChunk), onDelta(textChunk), onDone(donePayload), onError(err)
export async function streamChat(
  { messages, api = 'chat', maxTokens, temperature, thinking = true },
  { onReasoning, onDelta, onDone, onError, signal } = {},
) {
  const body = { messages, api, stream: true, thinking }
  if (maxTokens != null) body.max_tokens = maxTokens
  if (temperature != null) body.temperature = temperature

  let res
  try {
    res = await fetch(`${BASE}/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
      body: JSON.stringify(body),
      signal,
    })
  } catch (err) {
    onError?.(err?.message || 'Request failed')
    return
  }

  if (!res.ok || !res.body) {
    let detail = `HTTP ${res.status} on POST /chat`
    try {
      const j = await res.json()
      if (j?.detail) detail = j.detail
    } catch {
      /* non-JSON error body */
    }
    onError?.(detail)
    return
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let pending = ''

  const handleFrame = (frame) => {
    let event = 'message'
    const dataLines = []
    for (const raw of frame.split('\n')) {
      const line = raw.replace(/\r$/, '')
      if (line.startsWith('event:')) event = line.slice(6).trim()
      else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim())
    }
    if (dataLines.length === 0) return
    let data
    try {
      data = JSON.parse(dataLines.join('\n'))
    } catch {
      return
    }
    if (event === 'reasoning') onReasoning?.(data.text || '')
    else if (event === 'delta') onDelta?.(data.text || '')
    else if (event === 'done') onDone?.(data)
    else if (event === 'error') onError?.(data.message || 'Stream error')
  }

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      pending += decoder.decode(value, { stream: true })
      let idx
      while ((idx = pending.indexOf('\n\n')) !== -1) {
        const frame = pending.slice(0, idx)
        pending = pending.slice(idx + 2)
        if (frame.trim()) handleFrame(frame)
      }
    }
    if (pending.trim()) handleFrame(pending)
  } catch (err) {
    onError?.(err?.message || 'Stream interrupted')
  }
}
