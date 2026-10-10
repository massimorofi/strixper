import { useEffect, useRef, useState } from 'react'
import {
  Brain,
  ChevronDown,
  Eraser,
  Loader2,
  Send,
  Square,
  User,
  Wrench,
} from 'lucide-react'
import { createThinkSplitter, streamAgentChat, streamChat } from '../api.js'
import { fmtInt, fmtNum } from '../format.js'

const API_OPTIONS = [
  { value: 'chat', label: 'Chat Completions', path: '/v1/chat/completions' },
  { value: 'messages', label: 'Anthropic Messages', path: '/v1/messages' },
  { value: 'responses', label: 'Responses', path: '/v1/responses' },
  { value: 'completions', label: 'Text Completions', path: '/v1/completions' },
]

const STORAGE_KEY = 'strixper-chat'
const SETTINGS_KEY = 'strixper-chat-settings'
const MAX_STORED_MESSAGES = 100

// Chat preferences live apart from the transcript: they have to survive a
// cleared conversation, and a cleared conversation must not lose the mode
// the user chose.
const DEFAULT_SETTINGS = {
  agentMode: true,
  allowActions: false,
  fullAccess: false,
}

function loadSettings() {
  try {
    const raw = localStorage.getItem(SETTINGS_KEY)
    if (!raw) return { ...DEFAULT_SETTINGS }
    const parsed = JSON.parse(raw)
    return {
      agentMode:
        typeof parsed?.agentMode === 'boolean'
          ? parsed.agentMode
          : DEFAULT_SETTINGS.agentMode,
      allowActions:
        typeof parsed?.allowActions === 'boolean'
          ? parsed.allowActions
          : DEFAULT_SETTINGS.allowActions,
      fullAccess:
        typeof parsed?.fullAccess === 'boolean'
          ? parsed.fullAccess
          : DEFAULT_SETTINGS.fullAccess,
    }
  } catch {
    return { ...DEFAULT_SETTINGS }
  }
}

function persistSettings(settings) {
  try {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings))
  } catch {
    /* storage unavailable -- preferences stay for this session only */
  }
}

// Unique per message and independent of any module-level counter, so a hot
// module reload (which resets module state but preserves component state)
// can never hand out an id that is already in use.
const makeId = () =>
  `m${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`

// The completions style returns raw text with inline think markers, so the
// reasoning has to be split out client-side instead of arriving separately.
function usesInlineThink(api) {
  return api === 'completions'
}

// Build the history we send upstream. The backend rejects any message whose
// content is blank (min_length=1), and an assistant turn can legitimately
// end up empty -- reasoning only, or an error before any text arrived.
// Sending that back would fail validation and break the whole conversation,
// so empty turns are dropped here rather than at the far end.
const ROLES = ['user', 'assistant', 'system']

function toOutbound(messages) {
  return messages
    .filter(
      (m) =>
        ROLES.includes(m?.role) &&
        typeof m?.content === 'string' &&
        m.content.trim().length > 0,
    )
    .map(({ role, content }) => ({ role, content }))
}

function loadStoredMessages() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    // A stream interrupted by a page reload must not stay "streaming".
    const restored = parsed
      .filter((m) => m && typeof m.content === 'string')
      .map((m) => ({ ...m, streaming: false }))
    // Drop any entries sharing an id (a corrupted store would otherwise give
    // us duplicate React keys).
    const seen = new Set()
    return restored.filter((m) => {
      if (seen.has(m.id)) return false
      seen.add(m.id)
      return true
    })
  } catch {
    return []
  }
}

function ReasoningBlock({ text, streaming }) {
  const [open, setOpen] = useState(false)
  if (!text) return null
  return (
    <div className="mb-2 overflow-hidden rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-page)]">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-1.5 px-2.5 py-1.5 text-xs font-medium text-[var(--text-muted)] transition-colors hover:text-[var(--text-secondary)]"
      >
        <Brain size={13} className="text-[var(--series-1)]" />
        <span>Reasoning</span>
        <span className="text-[var(--text-muted)]">
          ({fmtInt(text.length)} chars)
        </span>
        <ChevronDown
          size={13}
          className={`ml-auto transition-transform ${open ? 'rotate-180' : ''}`}
        />
      </button>
      {open && (
        <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words px-2.5 py-2 font-sans text-xs italic leading-relaxed text-[var(--text-muted)]">
          {text}
          {streaming && <span className="animate-pulse">▍</span>}
        </pre>
      )}
    </div>
  )
}

// The agent's tool calls, collapsed. Deliberately styled like ReasoningBlock
// so an agentic reply looks like any other reply until you open it.
function StepsBlock({ steps, streaming }) {
  const [open, setOpen] = useState(false)
  if (!steps || steps.length === 0) return null
  return (
    <div className="mb-2 overflow-hidden rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-page)]">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-1.5 px-2.5 py-1.5 text-xs font-medium text-[var(--text-muted)] transition-colors hover:text-[var(--text-secondary)]"
      >
        <Wrench size={13} className="text-[var(--series-2)]" />
        <span>Actions</span>
        <span className="text-[var(--text-muted)]">({steps.length})</span>
        <ChevronDown
          size={13}
          className={`ml-auto transition-transform ${open ? 'rotate-180' : ''}`}
        />
      </button>
      {open && (
        <ol className="max-h-64 space-y-1.5 overflow-auto px-2.5 py-2">
          {steps.map((step, i) => (
            <li key={i} className="text-xs leading-relaxed">
              <span className="font-medium text-[var(--text-secondary)]">
                {i + 1}. {step.name}
              </span>
              {step.detail ? (
                <span className="text-[var(--text-muted)]"> ({step.detail})</span>
              ) : null}
              {step.output ? (
                <pre className="mt-0.5 max-h-32 overflow-auto whitespace-pre-wrap break-words rounded bg-[var(--surface-card)] px-2 py-1 font-mono text-[11px] text-[var(--text-muted)]">
                  {step.output}
                </pre>
              ) : step.phase === 'call' ? (
                <span className="ml-1 inline-flex items-center gap-1 text-[var(--text-muted)]">
                  <Loader2 className="h-3 w-3 animate-spin" />
                </span>
              ) : null}
            </li>
          ))}
          {streaming && (
            <li className="text-xs italic text-[var(--text-muted)]">
              Working…
            </li>
          )}
        </ol>
      )}
    </div>
  )
}

function Bubble({ msg }) {
  const isUser = msg.role === 'user'
  return (
    <div className={`flex gap-2.5 ${isUser ? 'flex-row-reverse' : 'flex-row'}`}>
      <div
        className={`mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-[var(--border-hairline)] ${
          isUser ? 'bg-[var(--series-1)] text-white' : 'bg-[var(--surface-card)]'
        }`}
      >
        {isUser ? (
          <User size={14} />
        ) : (
          <Brain size={14} className="text-[var(--series-1)]" />
        )}
      </div>
      <div
        className={`max-w-[85%] rounded-xl border px-3 py-2 ${
          isUser
            ? 'border-[var(--series-1)] bg-[var(--surface-card)]'
            : 'border-[var(--border-hairline)] bg-[var(--surface-card)]'
        }`}
      >
        {isUser ? (
          <p className="whitespace-pre-wrap break-words text-sm leading-relaxed text-[var(--text-primary)]">
            {msg.content}
          </p>
        ) : (
          <>
            <ReasoningBlock text={msg.reasoning} streaming={msg.streaming} />
            <StepsBlock steps={msg.steps} streaming={msg.streaming} />
            {msg.content ? (
              <p className="whitespace-pre-wrap break-words text-sm leading-relaxed text-[var(--text-primary)]">
                {msg.content}
                {msg.streaming && <span className="animate-pulse">▍</span>}
              </p>
            ) : msg.streaming ? (
              <p className="flex items-center gap-1.5 text-sm text-[var(--text-muted)]">
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                Thinking…
              </p>
            ) : null}
            {msg.error && (
              <p className="mt-1 text-xs text-[var(--status-critical)]">
                {msg.error}
              </p>
            )}
            {msg.stats && !msg.streaming && (
              <p className="mt-1.5 border-t border-[var(--border-hairline)] pt-1.5 text-[11px] text-[var(--text-muted)]">
                {msg.stats}
              </p>
            )}
          </>
        )}
      </div>
    </div>
  )
}

function formatStats(done) {
  const parts = []
  const usage = done?.usage || {}
  const prompt = usage.prompt_tokens ?? usage.input_tokens
  const completion = usage.completion_tokens ?? usage.output_tokens
  if (prompt != null || completion != null) {
    parts.push(`${fmtInt(prompt ?? 0)} in / ${fmtInt(completion ?? 0)} out`)
  }
  const t = done?.timings || {}
  if (t.predicted_per_second) {
    parts.push(`${fmtNum(t.predicted_per_second, 1)} tok/s`)
  }
  if (t.predicted_ms) {
    parts.push(`${fmtNum(t.predicted_ms / 1000, 2)}s`)
  }
  if (done?.tool_calls) parts.push(`${fmtInt(done.tool_calls)} tool call${done.tool_calls === 1 ? '' : 's'}`)
  if (done?.finish_reason) parts.push(done.finish_reason)
  return parts.join(' · ')
}

function persistMessages(messages) {
  try {
    const seen = new Set()
    const trimmed = messages
      .filter((m) => {
        if (seen.has(m.id)) return false
        seen.add(m.id)
        return true
      })
      .slice(-MAX_STORED_MESSAGES)
      .map((m) => ({
        id: m.id,
        role: m.role,
        content: m.content,
        reasoning: m.reasoning,
        steps: m.steps,
        stats: m.stats,
        error: m.error,
      }))
    localStorage.setItem(STORAGE_KEY, JSON.stringify(trimmed))
  } catch {
    /* storage full or unavailable -- conversation stays for this session only */
  }
}

export default function AIChatTab({ active = true }) {
  const [messages, setMessages] = useState(loadStoredMessages)
  const [draft, setDraft] = useState('')
  const [api, setApi] = useState('chat')
  const [thinking, setThinking] = useState(true)
  const [maxTokens, setMaxTokens] = useState('1024')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  // Agent mode hands the request to the tool-using agent instead of a
  // single upstream call. Actions lets that agent start and stop engines,
  // which reach docker and are disruptive, so it is a separate opt-in.
  const [settings, setSettings] = useState(loadSettings)
  const scrollRef = useRef(null)
  const abortRef = useRef(null)

  // Persist so the conversation survives reloads, not just tab switches.
  useEffect(() => {
    persistMessages(messages)
  }, [messages])

  useEffect(() => {
    persistSettings(settings)
  }, [settings])

  // While hidden the element has no layout, so scrolling only takes effect
  // once the tab is shown again.
  useEffect(() => {
    if (!active) return
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [active, messages])

  const stop = () => {
    abortRef.current?.abort()
    abortRef.current = null
    setBusy(false)
    setMessages((prev) =>
      prev.map((m) => (m.streaming ? { ...m, streaming: false } : m)),
    )
  }

  // Agentic turn: the backend runs tools and iterates, streaming the same
  // reasoning/delta vocabulary plus a `tool` event per call. Steps are
  // accumulated so the collapsed panel shows what was done and what came
  // back; a result replaces the pending call with the same name.
  const sendAgent = async (outbound, patch, controller) => {
    let reasoningAcc = ''
    let contentAcc = ''
    let steps = []

    try {
      await streamAgentChat(
        {
          messages: outbound,
          mode: 'agent',
          allowActions: settings.allowActions,
          fullAccess: settings.fullAccess,
          thinking,
          // max_tokens is deliberately omitted: a long write_file call puts
          // the whole file inside one tool-call JSON object, and a small
          // cap truncates those arguments mid-string. AGENT_MAX_TOKENS on
          // the backend sets a safe ceiling instead.
        },
        {
          signal: controller.signal,
          onReasoning: (chunk) => {
            reasoningAcc += chunk
            patch({ reasoning: reasoningAcc })
          },
          onDelta: (chunk) => {
            contentAcc += chunk
            patch({ content: contentAcc })
          },
          onTool: (data) => {
            if (data?.phase === 'call') {
              steps = [
                ...steps,
                { name: data.name || 'tool', detail: data.detail || '' },
              ]
            } else {
              const last = steps[steps.length - 1]
              if (last && !last.output) {
                steps = [
                  ...steps.slice(0, -1),
                  { ...last, output: data?.output || '', phase: 'result' },
                ]
              } else {
                steps = [
                  ...steps,
                  {
                    name: data?.name || 'tool',
                    output: data?.output || '',
                    phase: 'result',
                  },
                ]
              }
            }
            patch({ steps })
          },
          onDone: (done) => {
            patch({
              streaming: false,
              stats: formatStats(done),
              reasoning: reasoningAcc || undefined,
              content: contentAcc,
              steps,
            })
          },
          onError: (message) => {
            patch({ streaming: false, error: message, steps })
            setError(message)
          },
        },
      )
    } catch (err) {
      const message = err?.message || 'Request failed'
      patch({ streaming: false, error: message, steps })
      setError(message)
    } finally {
      setBusy(false)
      abortRef.current = null
    }
  }

  const send = async () => {
    const text = draft.trim()
    if (!text || busy) return

    const userMsg = { id: makeId(), role: 'user', content: text }
    const assistantId = makeId()
    setMessages((prev) => [
      ...prev,
      userMsg,
      {
        id: assistantId,
        role: 'assistant',
        content: '',
        reasoning: '',
        steps: [],
        streaming: true,
        stats: null,
        error: null,
      },
    ])
    setDraft('')
    setError(null)
    setBusy(true)

    const controller = new AbortController()
    abortRef.current = controller

    const outbound = toOutbound([...messages, userMsg])
    const patch = (changes) =>
      setMessages((prev) =>
        prev.map((m) => (m.id === assistantId ? { ...m, ...changes } : m)),
      )

    if (settings.agentMode) {
      await sendAgent(outbound, patch, controller)
      return
    }

    // Completions returns raw text with inline think markers; the other three
    // APIs deliver reasoning on a separate SSE channel.
    const splitter = usesInlineThink(api) ? createThinkSplitter() : null
    let reasoningAcc = ''
    let contentAcc = ''

    try {
      await streamChat(
        {
          messages: toOutbound([...messages, userMsg]),
          api,
          thinking,
          maxTokens: maxTokens ? Number(maxTokens) : undefined,
        },
        {
          signal: controller.signal,
          onReasoning: (chunk) => {
            reasoningAcc += chunk
            patch({ reasoning: reasoningAcc })
          },
          onDelta: (chunk) => {
            if (splitter) {
              const split = splitter(chunk)
              reasoningAcc += split.reasoning
              contentAcc += split.content
              patch({ reasoning: reasoningAcc, content: contentAcc })
            } else {
              contentAcc += chunk
              patch({ content: contentAcc })
            }
          },
          onDone: (done) => {
            patch({
              streaming: false,
              stats: formatStats(done),
              reasoning: done?.reasoning || reasoningAcc || undefined,
              content: done?.content || contentAcc,
            })
          },
          onError: (message) => {
            patch({ streaming: false, error: message })
            setError(message)
          },
        },
      )
    } catch (err) {
      const message = err?.message || 'Request failed'
      patch({ streaming: false, error: message })
      setError(message)
    } finally {
      setBusy(false)
      abortRef.current = null
    }
  }

  const onKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      if (busy) stop()
      else send()
    }
  }

  const clear = () => {
    stop()
    setMessages([])
    setError(null)
  }

  const activeApi = API_OPTIONS.find((o) => o.value === api)

  return (
    <div className="flex h-[calc(100vh-8.5rem)] flex-col overflow-hidden rounded-xl border border-[var(--border-hairline)] bg-[var(--surface-card)]">
      {/* Controls */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-[var(--border-hairline)] px-4 py-2.5">
        <div className="mr-auto">
          <h2 className="text-sm font-semibold text-[var(--text-primary)]">
            AI Chat
          </h2>
          <p className="text-xs text-[var(--text-muted)]">
            {settings.agentMode
              ? 'Agent · tool-driven multi-step'
              : `${activeApi?.label} · ${activeApi?.path}`}
          </p>
        </div>

        <label className="flex cursor-pointer items-center gap-1.5 text-xs text-[var(--text-secondary)]">
          <input
            type="checkbox"
            checked={settings.agentMode}
            onChange={(e) =>
              setSettings((s) => ({ ...s, agentMode: e.target.checked })
            )}
            disabled={busy}
            className="accent-[var(--series-1)]"
          />
          Agent
        </label>

        {settings.agentMode && (
          <label
            className="flex cursor-pointer items-center gap-1.5 text-xs text-[var(--text-secondary)]"
            title="Let the agent start and stop engine containers"
          >
            <input
              type="checkbox"
              checked={settings.allowActions}
              onChange={(e) =>
                setSettings((s) => ({ ...s, allowActions: e.target.checked })
              )}
              disabled={busy}
              className="accent-[var(--status-warning)]"
            />
            Allow actions
          </label>
        )}

        {settings.agentMode && (
          <label
            className="flex cursor-pointer items-center gap-1.5 text-xs text-[var(--text-secondary)]"
            title="Let the agent browse the web, run shell commands, execute Python, and write files. The container has docker.sock mounted, so this is effectively root on the host."
          >
            <input
              type="checkbox"
              checked={settings.fullAccess}
              onChange={(e) =>
                setSettings((s) => ({
                  ...s,
                  fullAccess: e.target.checked,
                  // The backend treats full access as a superset; keep the
                  // checkboxes honest about what is actually enabled.
                  allowActions: e.target.checked ? true : s.allowActions,
                }))
              }
              disabled={busy}
              className="accent-[var(--status-critical)]"
            />
            Full access
          </label>
        )}

        {!settings.agentMode && (
          <label className="flex items-center gap-2 text-xs text-[var(--text-secondary)]">
            API
            <select
              value={api}
              onChange={(e) => setApi(e.target.value)}
              disabled={busy}
              className="rounded-md border border-[var(--border-hairline)] bg-[var(--surface-page)] px-2 py-1 text-xs text-[var(--text-primary)] focus:outline-none focus:ring-2 focus:ring-[var(--series-1)] disabled:opacity-50"
            >
              {API_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </label>
        )}

        {!settings.agentMode && (
          <label className="flex items-center gap-2 text-xs text-[var(--text-secondary)]">
            Max tokens
            <input
              type="number"
              min="1"
              max="32768"
              value={maxTokens}
              onChange={(e) => setMaxTokens(e.target.value)}
              disabled={busy}
              className="w-24 rounded-md border border-[var(--border-hairline)] bg-[var(--surface-page)] px-2 py-1 text-xs text-[var(--text-primary)] focus:outline-none focus:ring-2 focus:ring-[var(--series-1)] disabled:opacity-50"
            />
          </label>
        )}

        <label className="flex cursor-pointer items-center gap-1.5 text-xs text-[var(--text-secondary)]">
          <input
            type="checkbox"
            checked={thinking}
            onChange={(e) => setThinking(e.target.checked)}
            disabled={busy}
            className="accent-[var(--series-1)]"
          />
          Thinking
        </label>

        <button
          type="button"
          onClick={clear}
          disabled={busy || messages.length === 0}
          title="Clear conversation"
          className="rounded-md border border-[var(--border-hairline)] p-1.5 text-[var(--text-secondary)] transition-colors hover:bg-[var(--surface-page)] hover:text-[var(--text-primary)] disabled:cursor-not-allowed disabled:opacity-50"
        >
          <Eraser size={14} />
        </button>
      </div>

      {/* Messages */}
      <div ref={scrollRef} className="flex-1 space-y-4 overflow-y-auto px-4 py-4">
        {messages.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-2 text-center">
            <Brain size={28} className="text-[var(--text-muted)]" />
            <p className="text-sm text-[var(--text-secondary)]">
              Ask the model anything.
            </p>
            <p className="max-w-sm text-xs text-[var(--text-muted)]">
              Responses stream live. Chain-of-thought and any tool calls are
              collapsed under each reply. Turn Agent off to pick a raw API
              style and compare Halogen endpoints directly.
            </p>
          </div>
        ) : (
          messages.map((m) => <Bubble key={m.id} msg={m} />)
        )}
      </div>

      {/* Composer */}
      <div className="border-t border-[var(--border-hairline)] px-4 py-3">
        {error && (
          <div className="mb-2 rounded-lg border border-[var(--status-critical)] px-3 py-1.5 text-xs text-[var(--status-critical)]">
            {error}
          </div>
        )}
        <div className="flex items-end gap-2">
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={onKeyDown}
            rows={2}
            placeholder="Type a message… (Enter to send, Shift+Enter for a new line)"
            className="flex-1 resize-none rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-page)] px-3 py-2 text-sm text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:border-[var(--series-1)] focus:outline-none"
          />
          <button
            type="button"
            onClick={busy ? stop : send}
            disabled={!busy && !draft.trim()}
            className={`inline-flex h-9 items-center gap-1.5 rounded-lg px-4 text-sm font-medium transition-colors ${
              busy
                ? 'border border-[var(--status-critical)] text-[var(--status-critical)] hover:bg-[var(--surface-page)]'
                : 'bg-[var(--series-1)] text-white hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40'
            }`}
          >
            {busy ? (
              <>
                <Square size={13} />
                Stop
              </>
            ) : (
              <>
                <Send size={14} />
                Send
              </>
            )}
          </button>
        </div>
      </div>
    </div>
  )
}
