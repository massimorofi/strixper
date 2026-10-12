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

// ---------------------------------------------------------------------------
// Chat streaming retry policy
//
// A long chat used to die on the first dropped connection with a "network
// error" bubble. The backend retries the upstream engine call, but the
// browser -> backend hop needs its own retry: that is where a Wi-Fi blip,
// a backend restart, or a proxy idle timeout shows up.
//
// 6 attempts total (the first try plus 5 retries), 10s apart.
// ---------------------------------------------------------------------------
const CHAT_RETRY_ATTEMPTS = 6
const CHAT_RETRY_PAUSE_MS = 10_000

// Statuses that mean "the server hiccuped", not "your request is wrong".
const RETRYABLE_STATUS = new Set([408, 409, 425, 429, 500, 502, 503, 504])

function isAborted(err, signal) {
  if (signal?.aborted) return true
  const name = err?.name
  return name === 'AbortError' || name === 'CanceledError'
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

// Raised internally so a failure inside the SSE handler can break out of the
// read loop and be classified by the retry loop.
class ChatStreamError extends Error {
  constructor(message, retryable) {
    super(message)
    this.name = 'ChatStreamError'
    this.retryable = !!retryable
  }
}

/**
 * POST a chat request and read the SSE response, retrying transient network
 * failures up to `attempts` times with `pauseMs` between tries.
 *
 * Retry rules:
 *  - A user abort is never retried and never reported as an error.
 *  - A 4xx response (other than the retryable statuses) is reported at once:
 *    retrying a validation error just wastes 10s each time.
 *  - Once any answer text has reached the screen, a failure is reported rather
 *    than retried, because replaying the request would duplicate the answer.
 *
 * `handlers` receives {onReasoning, onDelta, onDone, onError, onRetry}.
 */
async function streamChatWithRetry(path, body, handlers = {}, options = {}) {
  const attempts = options.attempts ?? CHAT_RETRY_ATTEMPTS
  const pauseMs = options.pauseMs ?? CHAT_RETRY_PAUSE_MS
  const signal = options.signal
  const { onReasoning, onDelta, onTool, onDone, onError, onRetry } = handlers

  let lastMessage = 'Network request failed'

  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    // Anything already shown to the user makes a replay unsafe.
    let emitted = false
    try {
      const res = await fetch(`${BASE}${path}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
        body: JSON.stringify(body),
        signal,
      })

      if (!res.ok || !res.body) {
        const message = await readError(res, `HTTP ${res.status} on POST ${path}`)
        throw new ChatStreamError(message, RETRYABLE_STATUS.has(res.status))
      }

      await readSSE(res, (event, data) => {
        if (event === 'reasoning') {
          emitted = true
          onReasoning?.(data.text || '')
        } else if (event === 'delta') {
          emitted = true
          onDelta?.(data.text || '')
        } else if (event === 'done') {
          onDone?.(data)
        } else if (event === 'tool') {
          onTool?.(data)
        } else if (event === 'error') {
          // The backend has already retried this upstream failure out of its
          // own budget; replaying it here would multiply the waits.
          throw new ChatStreamError(textOr('Stream error', data?.message), false)
        }
      })
      return
    } catch (err) {
      if (isAborted(err, signal)) return
      lastMessage = err?.message || lastMessage
      const retryable =
        err instanceof ChatStreamError ? err.retryable && !emitted : !emitted
      if (attempt >= attempts || !retryable) {
        onError?.(lastMessage)
        return
      }
      onRetry?.(attempt, attempts, lastMessage)
      if (pauseMs) await sleep(pauseMs)
    }
  }
}

// Stream a chat turn from the backend /api/v1/chat proxy.
// onReasoning(textChunk), onDelta(textChunk), onDone(donePayload), onError(err)
export async function streamChat(
  { messages, api = 'chat', maxTokens, temperature, thinking = true, runId },
  { onReasoning, onDelta, onDone, onError, onRetry, signal } = {},
) {
  const body = { messages, api, stream: true, thinking }
  if (runId) body.run_id = runId
  if (maxTokens != null) body.max_tokens = maxTokens
  if (temperature != null) body.temperature = temperature

  await streamChatWithRetry(
    '/chat',
    body,
    { onReasoning, onDelta, onDone, onError, onRetry },
    { signal },
  )
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

// -- Agent skills and MCP extensions --------------------------------------

export const fetchAgentSkills = () => getJSON('/agent/skills')

async function sendMCPJSON(method, path, token, body) {
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: {
      Accept: 'application/json',
      'Content-Type': 'application/json',
      'X-MCP-Admin-Token': token,
    },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  })
  if (!res.ok) {
    throw new Error(await readError(res, `HTTP ${res.status} on ${method} ${path}`))
  }
  return res.status === 204 ? null : res.json()
}

export const fetchMCPServers = (token) =>
  sendMCPJSON('GET', '/agent/mcp-servers', token)
export const saveMCPServer = (token, server, exists) =>
  sendMCPJSON(
    exists ? 'PUT' : 'POST',
    exists ? `/agent/mcp-servers/${encodeURIComponent(server.id)}` : '/agent/mcp-servers',
    token,
    server,
  )
export const deleteMCPServer = (token, id) =>
  sendMCPJSON('DELETE', `/agent/mcp-servers/${encodeURIComponent(id)}`, token)
export const testMCPServer = (token, server) =>
  sendMCPJSON('POST', '/agent/mcp-servers/test', token, server)

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

  await streamChatWithRetry(
    '/agent/chat',
    body,
    { onReasoning, onDelta, onTool, onDone, onError, onRetry },
    { signal },
  )
}

// ---------------------------------------------------------------------------
// Files & workspace
//
// Uploads go out as multipart/form-data. Everything else is plain JSON.
// Stored files are addressed by the opaque file_id the backend hands back,
// never by a path the browser supplies.
// ---------------------------------------------------------------------------

export async function uploadFile(file, { subdir } = {}) {
  const form = new FormData()
  form.append('file', file, file.name || 'upload')
  if (subdir) form.append('subdir', subdir)

  let res
  try {
    res = await fetch(`${BASE}/files/upload`, {
      method: 'POST',
      body: form,
    })
  } catch (err) {
    throw new Error(err?.message || 'Upload failed')
  }
  if (!res.ok) {
    throw new Error(await readError(res, `HTTP ${res.status} on POST /files/upload`))
  }
  return res.json()
}

export const listFiles = () => getJSON('/files')

export const listWorkspace = (path = '') =>
  getJSON(`/workspace/list${path ? `?path=${encodeURIComponent(path)}` : ''}`)

export const readWorkspaceFile = (path) =>
  getJSON(`/workspace/read?path=${encodeURIComponent(path)}`)

export async function deleteWorkspacePath(path, { recursive = false } = {}) {
  const params = new URLSearchParams({ path, recursive: String(recursive) })
  const res = await fetch(`${BASE}/workspace?${params.toString()}`, {
    method: 'DELETE',
  })
  if (!res.ok) {
    throw new Error(await readError(res, `HTTP ${res.status} on DELETE /workspace`))
  }
  return res.json()
}

// Bulk delete. Recursive by default: the UI asks for confirmation before
// calling this, and a directory selection that silently skipped its
// contents would be worse than one that removes them.
export async function deleteWorkspacePaths(paths, { recursive = true } = {}) {
  const res = await fetch(`${BASE}/workspace/delete`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ paths, recursive }),
  })
  if (!res.ok) {
    throw new Error(
      await readError(res, `HTTP ${res.status} on POST /workspace/delete`),
    )
  }
  return res.json()
}

export async function deleteFile(fileId) {
  const res = await fetch(`${BASE}/files/${encodeURIComponent(fileId)}`, {
    method: 'DELETE',
  })
  if (!res.ok) {
    throw new Error(await readError(res, `HTTP ${res.status} on DELETE /files`))
  }
  return res.json()
}

// Fetch a stored file and hand it to the browser as a download. Object URLs
// are revoked by the caller once the anchor has been clicked.
export async function downloadFile(fileId, filename) {
  const res = await fetch(`${BASE}/files/${encodeURIComponent(fileId)}`)
  if (!res.ok) {
    throw new Error(await readError(res, `HTTP ${res.status} on GET /files`))
  }
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  try {
    const a = document.createElement('a')
    a.href = url
    a.download = filename || 'download'
    document.body.appendChild(a)
    a.click()
    a.remove()
  } finally {
    // Give the browser a moment to start the download before reclaiming.
    setTimeout(() => URL.revokeObjectURL(url), 5000)
  }
  return true
}

// Path-addressed workspace download (agent output has no upload record).
export async function downloadWorkspaceFile(path, filename) {
  const res = await fetch(
    `${BASE}/workspace/download?path=${encodeURIComponent(path)}`,
  )
  if (!res.ok) {
    throw new Error(
      await readError(res, `HTTP ${res.status} on GET /workspace/download`),
    )
  }
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  try {
    const a = document.createElement('a')
    a.href = url
    a.download = filename || path.split('/').pop() || 'download'
    document.body.appendChild(a)
    a.click()
    a.remove()
  } finally {
    setTimeout(() => URL.revokeObjectURL(url), 5000)
  }
  return true
}

export const workspacePreviewUrl = (path) =>
  `${BASE}/workspace/preview?path=${encodeURIComponent(path)}`
