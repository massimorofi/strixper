const BASE = '/api/v1'

// Turn a FastAPI error body into a string that is safe to render.
//
// `detail` is a plain string when it comes from HTTPException, but a list of
// structured objects ({type, loc, msg, input, ctx}) when FastAPI rejects the
// request body itself. Handing that list to React as a child crashes the render
// with "Objects are not valid as a React child", which blanks the whole page --
// so it is flattened here, once, at the boundary.
function formatIssue(issue) {
  if (typeof issue === 'string') return issue
  if (!issue || typeof issue !== 'object') return String(issue)
  const msg = typeof issue.msg === 'string' ? issue.msg : 'invalid value'
  const path = Array.isArray(issue.loc)
    ? issue.loc.filter((p) => p !== 'body').join('.')
    : ''
  return path ? `${path}: ${msg}` : msg
}

function textOr(fallback, value) {
  if (typeof value === 'string' && value.trim()) return value
  if (Array.isArray(value) && value.length > 0) {
    const joined = value.map(formatIssue).filter(Boolean).join('; ')
    if (joined) return joined
  }
  return fallback
}

function errorText(fallback, body) {
  return textOr(fallback, body?.detail)
}

// Read the body of a failed response, always yielding a human-readable string.
async function readError(res, fallback) {
  let body = null
  try {
    body = await res.json()
  } catch {
    /* non-JSON error body */
  }
  return errorText(fallback, body)
}

async function getJSON(path) {
  const res = await fetch(`${BASE}${path}`, { headers: { Accept: 'application/json' } })
  if (!res.ok) {
    throw new Error(await readError(res, `HTTP ${res.status} on ${path}`))
  }
  return res.json()
}

export const fetchLiveStatus = () => getJSON('/live-status')
export const fetchTuningCheck = () => getJSON('/tuning-check')
export const fetchSystemInfo = () => getJSON('/system-info')
export const fetchConfig = () => getJSON('/config')

export async function countTokens(text, system) {
  const res = await fetch(`${BASE}/count-tokens`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(system ? { text, system } : { text }),
  })
  if (!res.ok) {
    throw new Error(await readError(res, `HTTP ${res.status} on POST /count-tokens`))
  }
  return res.json()
}

export async function postConfig(body) {
  const res = await fetch(`${BASE}/config`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    // Surface the backend's own explanation (e.g. a malformed engine
    // address) rather than a bare status code.
    throw new Error(await readError(res, `HTTP ${res.status} on POST /config`))
  }
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
    throw new Error(await readError(res, `HTTP ${res.status} on ${method} ${path}`))
  }
  return res.json()
}

export const createRunnerConfig = (cfg) => sendJSON('POST', '/runner/configs', cfg)
export const updateRunnerConfig = (id, cfg) =>
  sendJSON('PUT', `/runner/configs/${id}`, cfg)
export const deleteRunnerConfig = (id) =>
  fetch(`${BASE}/runner/configs/${id}`, { method: 'DELETE' }).then(async (res) => {
    if (!res.ok) {
      throw new Error(
        await readError(res, `HTTP ${res.status} on DELETE /runner/configs/${id}`),
      )
    }
    return res.json()
  })
export const stopRunnerRun = (runId) =>
  sendJSON('POST', `/runner/runs/${runId}/stop`, {})
export const fetchRunnerRuns = () => getJSON('/runner/runs').then((j) => j?.runs || [])
export const fetchRunnerLiveStatus = (runId) =>
  getJSON(`/runner/runs/${runId}/live-status`)

// Render a command template against its parameters without running it.
// Returns { command }; throws with the backend's message on a bad template.
export const previewRunnerCommand = (docker_command, parameters = []) =>
  sendJSON('POST', '/runner/preview', { docker_command, parameters })

// Shared Server-Sent Events reader. Splits the byte stream on blank-line
// frame boundaries and hands each complete frame to `onEvent(event, data)`.
// Unknown event names are passed through; a frame whose data is not JSON is
// skipped rather than treated as a failure.
async function readSSE(res, onEvent) {
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
    onEvent(event, data)
  }

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
}

// Start a configuration's engine. Returns the run summary; the console is
// a separate call (openRunnerStream), so starting never depends on holding
// a connection open.
export const startRunnerRun = (configId, values) =>
  sendJSON('POST', `/runner/configs/${configId}/run`, values ? { values } : {})

// The run currently live, or null. Polled so a run that exits on its own
// shows up in the UI without a stream being open.
export const fetchRunnerActive = async () => {
  const j = await getJSON('/runner/active')
  return j?.active || null
}

// Watch a live run: `state` (with backlog) first, then `stdout` chunks,
// then `exit`. Any number of browsers may attach to the same run.
export async function openRunnerStream(
  runId,
  { onState, onStdout, onExit, onError, signal } = {},
) {
  let res
  try {
    res = await fetch(`${BASE}/runner/runs/${runId}/stream`, {
      headers: { Accept: 'text/event-stream' },
      signal,
    })
  } catch (err) {
    if (err?.name !== 'AbortError') onError?.(err?.message || 'Request failed')
    return
  }

  if (!res.ok || !res.body) {
    onError?.(
      await readError(
        res,
        `HTTP ${res.status} on GET /runner/runs/${runId}/stream`,
      ),
    )
    return
  }

  try {
    await readSSE(res, (event, data) => {
      if (event === 'state') onState?.(data)
      else if (event === 'stdout') onStdout?.(data.text || '')
      else if (event === 'exit') onExit?.(data)
      else if (event === 'error')
        onError?.(textOr('Stream error', data?.message))
    })
  } catch (err) {
    if (err?.name !== 'AbortError') onError?.(err?.message || 'Stream interrupted')
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
  { messages, api = 'chat', maxTokens, temperature, thinking = true, runId },
  { onReasoning, onDelta, onDone, onError, signal } = {},
) {
  const body = { messages, api, stream: true, thinking }
  if (runId) body.run_id = runId
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
    onError?.(await readError(res, `HTTP ${res.status} on POST /chat`))
    return
  }

  try {
    await readSSE(res, (event, data) => {
      if (event === 'reasoning') onReasoning?.(data.text || '')
      else if (event === 'delta') onDelta?.(data.text || '')
      else if (event === 'done') onDone?.(data)
      else if (event === 'error')
        onError?.(textOr('Stream error', data?.message))
    })
  } catch (err) {
    if (err?.name !== 'AbortError') onError?.(err?.message || 'Stream interrupted')
  }
}

// Stream an agentic turn from the backend /api/v1/agent/chat endpoint.
//
// Same wire vocabulary as streamChat, plus one extra event:
//   tool  {phase: 'call'|'result', name, detail|output}
// so a client that ignores it still renders the answer correctly.
//
// onReasoning(textChunk), onDelta(textChunk), onTool({phase,name,...}),
// onDone(donePayload), onError(err)
export async function streamAgentChat(
  {
    messages,
    mode = 'agent',
    allowActions = false,
    fullAccess = false,
    maxTurns,
    maxTokens,
    temperature,
    thinking = true,
    runId,
  },
  { onReasoning, onDelta, onTool, onDone, onError, signal } = {},
) {
  const body = {
    messages,
    mode,
    allow_actions: allowActions,
    full_access: fullAccess,
    thinking,
  }
  if (runId) body.run_id = runId
  if (maxTurns != null) body.max_turns = maxTurns
  if (maxTokens != null) body.max_tokens = maxTokens
  if (temperature != null) body.temperature = temperature

  let res
  try {
    res = await fetch(`${BASE}/agent/chat`, {
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
    onError?.(await readError(res, `HTTP ${res.status} on POST /agent/chat`))
    return
  }

  try {
    await readSSE(res, (event, data) => {
      if (event === 'reasoning') onReasoning?.(data.text || '')
      else if (event === 'delta') onDelta?.(data.text || '')
      else if (event === 'tool') onTool?.(data)
      else if (event === 'done') onDone?.(data)
      else if (event === 'error') onError?.(textOr('Stream error', data?.message))
    })
  } catch (err) {
    if (err?.name !== 'AbortError') onError?.(err?.message || 'Stream interrupted')
  }
}
