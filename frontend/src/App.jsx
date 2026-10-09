import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import TopBar from './components/TopBar.jsx'
import LiveTab from './components/LiveTab.jsx'
import TuningTab from './components/TuningTab.jsx'
import AIChatTab from './components/AIChatTab.jsx'
import { fetchLiveStatus, postConfig } from './api.js'

const MAX_SAMPLES = 120

const VALID_TABS = ['live', 'tuning', 'chat']

export default function App() {
  const [tab, setTab] = useState(() => {
    const h = window.location.hash.replace('#', '')
    return VALID_TABS.includes(h) ? h : 'live'
  })
  const [intervalMs, setIntervalMs] = useState(5000)
  const [theme, setTheme] = useState(() => {
    try {
      return localStorage.getItem('strixper-theme') || 'dark'
    } catch {
      return 'dark'
    }
  })
  const [history, setHistory] = useState([])

  useEffect(() => {
    document.documentElement.classList.toggle('dark', theme === 'dark')
    try {
      localStorage.setItem('strixper-theme', theme)
    } catch {
      /* storage unavailable -- theme stays for this session only */
    }
  }, [theme])

  const { data, isError, refetch } = useQuery({
    queryKey: ['live-status'],
    queryFn: fetchLiveStatus,
    refetchInterval: intervalMs > 0 ? intervalMs : false,
  })

  useEffect(() => {
    if (!data) return
    setHistory((prev) => {
      const sample = {
        time: data.timestamp,
        promptTps: data.halogen?.prompt_tokens_per_sec ?? 0,
        decodeTps: data.halogen?.predicted_tokens_per_sec ?? 0,
        kvPct: (data.halogen?.kv_cache_usage_ratio ?? 0) * 100,
        queued: data.halogen?.queued ?? 0,
        gttPct: data.hardware?.gpu?.gtt_usage_pct ?? 0,
        gttUsedGb: data.hardware?.gpu?.gtt_used_gb ?? 0,
        vramPct: data.hardware?.rocm?.vram_usage_pct ?? 0,
        gpuUtilPct: data.hardware?.rocm?.gpu_util_pct ?? 0,
        ramPct: data.hardware?.memory?.ram_usage_pct ?? 0,
        cpuPct: data.hardware?.cpu?.usage_pct ?? 0,
      }
      const next = [...prev, sample]
      return next.length > MAX_SAMPLES ? next.slice(next.length - MAX_SAMPLES) : next
    })
  }, [data])

  const handleIntervalChange = (ms) => {
    setIntervalMs(ms)
    // Keep the backend poller in sync with the UI cadence.
    postConfig({ poll_interval_seconds: Math.max(ms / 1000, 0.5) }).catch(() => {})
  }

  const connected = data?.connected ?? false

  return (
    <div className="min-h-screen bg-[var(--surface-page)]">
      <TopBar
        connected={connected}
        intervalMs={intervalMs}
        onIntervalChange={handleIntervalChange}
        onRefresh={() => refetch()}
        theme={theme}
        onToggleTheme={() => setTheme((t) => (t === 'dark' ? 'light' : 'dark'))}
        tab={tab}
        onTabChange={setTab}
      />
      <main className="mx-auto max-w-7xl px-4 py-6">
        {isError && (
          <div className="mb-4 rounded-lg border border-[var(--status-critical)] bg-[var(--surface-card)] px-4 py-2 text-sm text-[var(--status-critical)]">
            Backend unreachable. Is the dashboard API running on port 8000?
          </div>
        )}
        {tab === 'live' && (
          <LiveTab
            data={data}
            history={history}
            theme={theme}
            onResetStats={() => refetch()}
          />
        )}
        {tab === 'tuning' && <TuningTab />}
        {/* The chat stays mounted (hidden via CSS) so switching tabs never
            discards the conversation, the draft, or an in-flight stream. */}
        <div className={tab === 'chat' ? '' : 'hidden'}>
          <AIChatTab active={tab === 'chat'} />
        </div>
      </main>
    </div>
  )
}
