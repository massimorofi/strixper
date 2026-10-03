import { Activity, Moon, RefreshCw, Sun, Wifi, WifiOff } from 'lucide-react'

const REFRESH_OPTIONS = [
  { value: 1000, label: '1s' },
  { value: 5000, label: '5s' },
  { value: 10000, label: '10s' },
  { value: 30000, label: '30s' },
  { value: 0, label: 'Off' },
]

export default function TopBar({
  connected,
  intervalMs,
  onIntervalChange,
  onRefresh,
  theme,
  onToggleTheme,
  tab,
  onTabChange,
}) {
  return (
    <header className="sticky top-0 z-20 border-b border-[var(--border-hairline)] bg-[var(--surface-card)]">
      <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-6 gap-y-3 px-4 py-3">
        <div className="flex items-center gap-2">
          <Activity size={20} className="text-[var(--series-1)]" />
          <h1 className="text-base font-semibold tracking-tight text-[var(--text-primary)]">
            Halogen Strix Halo Operations Dashboard
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
