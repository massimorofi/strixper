import { useState } from 'react'
import {
  Activity,
  Check,
  Moon,
  RefreshCw,
  Server,
  Sun,
  Wifi,
  WifiOff,
  X,
} from 'lucide-react'

const REFRESH_OPTIONS = [
  { value: 1000, label: '1s' },
  { value: 5000, label: '5s' },
  { value: 10000, label: '10s' },
  { value: 30000, label: '30s' },
  { value: 0, label: 'Off' },
]

/**
 * Which engine the backend is currently polling. Normally this follows the
 * LLM-Runner automatically; the inline editor is the manual override for
 * engines started outside it.
 */
function EngineChip({ host, configName, onChange }) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(host)
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  const startEdit = () => {
    setDraft(host)
    setError('')
    setEditing(true)
  }

  const save = async () => {
    const next = draft.trim()
    if (!next) {
      setError('Enter an address, e.g. http://127.0.0.1:18080')
      return
    }
    setSaving(true)
    setError('')
    try {
      await onChange(next)
      setEditing(false)
    } catch (err) {
      setError(err?.message || 'Could not update the engine address')
    } finally {
      setSaving(false)
    }
  }

  if (editing) {
    return (
      <div className="flex flex-col gap-1">
        <div className="flex items-center gap-2">
          <input
            autoFocus
            type="text"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') save()
              if (e.key === 'Escape') setEditing(false)
            }}
            placeholder="http://127.0.0.1:18080"
            spellCheck={false}
            className="w-60 rounded-md border border-[var(--border-hairline)] bg-[var(--surface-page)] px-2 py-1 font-mono text-xs text-[var(--text-primary)] focus:border-[var(--series-1)] focus:outline-none"
          />
          <button
            type="button"
            onClick={save}
            disabled={saving}
            title="Point the dashboard at this address"
            className="rounded-md bg-[var(--series-1)] p-1.5 text-white transition-opacity hover:opacity-90 disabled:opacity-40"
          >
            <Check size={14} />
          </button>
          <button
            type="button"
            onClick={() => setEditing(false)}
            title="Cancel"
            className="rounded-md border border-[var(--border-hairline)] p-1.5 text-[var(--text-secondary)] transition-colors hover:bg-[var(--surface-page)]"
          >
            <X size={14} />
          </button>
        </div>
        {error && (
          <span className="text-xs text-[var(--status-critical)]">{error}</span>
        )}
      </div>
    )
  }

  return (
    <button
      type="button"
      onClick={startEdit}
      title="Click to change which engine the dashboard reads"
      className="inline-flex items-center gap-1.5 rounded-md border border-[var(--border-hairline)] px-2 py-1 text-xs text-[var(--text-muted)] transition-colors hover:bg-[var(--surface-page)] hover:text-[var(--text-primary)]"
    >
      <Server size={13} className="shrink-0" />
      <span className="font-mono">{host || 'no engine set'}</span>
      {configName && <span className="text-[var(--text-secondary)]">· {configName}</span>}
    </button>
  )
}

export default function TopBar({
  connected,
  intervalMs,
  onIntervalChange,
  onRefresh,
  theme,
  onToggleTheme,
  tab,
  onTabChange,
  engineHost,
  engineConfigName,
  onEngineChange,
}) {
  return (
    <header className="sticky top-0 z-20 border-b border-[var(--border-hairline)] bg-[var(--surface-card)]">
      <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-6 gap-y-3 px-4 py-3">
        <div className="flex items-center gap-2">
          <img src="/icon-192.png" alt="Strix Halo" className="h-8 w-8" />
          <Activity size={20} className="text-[var(--series-1)]" />
          <h1 className="text-base font-semibold tracking-tight text-[var(--text-primary)]">
             Strix Halo LLM Operations Dashboard
          </h1>
        </div>

        <div className="flex items-center gap-2 text-sm text-[var(--text-secondary)]">
          {connected ? (
            <span className="inline-flex items-center gap-1.5">
              <span className="h-2.5 w-2.5 rounded-full bg-[var(--status-good)]" />
              Connected
            </span>
          ) : (
            <span className="inline-flex items-center gap-1.5 text-[var(--status-critical)]">
              <WifiOff size={15} />
              Connection Lost
            </span>
          )}
        </div>

        <EngineChip
          host={engineHost}
          configName={engineConfigName}
          onChange={onEngineChange}
        />

        <div className="ml-auto flex items-center gap-3">
          <label className="flex items-center gap-2 text-sm text-[var(--text-secondary)]">
            Auto-refresh
            <select
              value={intervalMs}
              onChange={(e) => onIntervalChange(Number(e.target.value))}
              className="rounded-md border border-[var(--border-hairline)] bg-[var(--surface-page)] px-2 py-1 text-sm text-[var(--text-primary)] focus:outline-none focus:ring-2 focus:ring-[var(--series-1)]"
            >
              {REFRESH_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </label>

          <button
            type="button"
            onClick={onRefresh}
            title="Refresh now"
            className="rounded-md border border-[var(--border-hairline)] p-2 text-[var(--text-secondary)] transition-colors hover:bg-[var(--surface-page)] hover:text-[var(--text-primary)]"
          >
            <RefreshCw size={15} />
          </button>

          <button
            type="button"
            onClick={onToggleTheme}
            title="Toggle theme"
            className="rounded-md border border-[var(--border-hairline)] p-2 text-[var(--text-secondary)] transition-colors hover:bg-[var(--surface-page)] hover:text-[var(--text-primary)]"
          >
            {theme === 'dark' ? <Sun size={15} /> : <Moon size={15} />}
          </button>
        </div>

        <nav className="flex w-full gap-1 sm:w-auto">
          {[
            { id: 'live', label: 'Live Server Metrics' },
            { id: 'tuning', label: 'Strix Halo Fine-Tuning' },
            { id: 'chat', label: 'AI-Chat' },
            { id: 'runner', label: 'LLM-Runner' },
          ].map((t) => (
            <button
              key={t.id}
              type="button"
              onClick={() => onTabChange(t.id)}
              className={`rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
                tab === t.id
                  ? 'bg-[var(--series-1)] text-white'
                  : 'text-[var(--text-secondary)] hover:bg-[var(--surface-page)]'
              }`}
            >
              {t.label}
            </button>
          ))}
        </nav>
      </div>
    </header>
  )
}
