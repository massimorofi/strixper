import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import TopBar from './components/TopBar.jsx'
import LiveTab from './components/LiveTab.jsx'
import TuningTab from './components/TuningTab.jsx'
import AIChatTab from './components/AIChatTab.jsx'
import RunnerTab from './components/RunnerTab.jsx'
import ErrorBoundary from './components/ErrorBoundary.jsx'
import {
  fetchConfig,
  fetchLiveStatus,
  fetchRunnerLiveStatus,
  fetchRunnerRuns,
  postConfig,
} from './api.js'

const MAX_SAMPLES = 120

const VALID_TABS = ['live', 'tuning', 'chat', 'runner']

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
  const [runHistories, setRunHistories] = useState({})
  const [selectedRunId, setSelectedRunId] = useState('')

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

  const { data: runs = [] } = useQuery({
    queryKey: ['runner-runs'],
    queryFn: fetchRunnerRuns,
    refetchInterval: 3000,
  })
  const runningRuns = runs.filter((run) => run.status === 'running')
  const selectedRun = runningRuns.find((run) => run.run_id === selectedRunId) || runningRuns[0]
  const { data: selectedRunData } = useQuery({
    queryKey: ['runner-live-status', selectedRun?.run_id],
    queryFn: () => fetchRunnerLiveStatus(selectedRun.run_id),
    enabled: Boolean(selectedRun?.run_id),
    refetchInterval: intervalMs > 0 ? intervalMs : false,
  })
  const displayedData = selectedRun ? (selectedRunData || data) : data
  const displayedHistory = selectedRun
    ? runHistories[selectedRun.run_id] || []
    : history

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

  useEffect(() => {
    if (!selectedRun || !selectedRunData) return
    setRunHistories((prev) => {
      const prior = prev[selectedRun.run_id] || []
      const sample = {
        time: selectedRunData.timestamp,
        promptTps: selectedRunData.halogen?.prompt_tokens_per_sec ?? 0,
        decodeTps: selectedRunData.halogen?.predicted_tokens_per_sec ?? 0,
        kvPct: (selectedRunData.halogen?.kv_cache_usage_ratio ?? 0) * 100,
        queued: selectedRunData.halogen?.queued ?? 0,
        gttPct: selectedRunData.hardware?.gpu?.gtt_usage_pct ?? 0,
        gttUsedGb: selectedRunData.hardware?.gpu?.gtt_used_gb ?? 0,
        vramPct: selectedRunData.hardware?.rocm?.vram_usage_pct ?? 0,
        gpuUtilPct: selectedRunData.hardware?.rocm?.gpu_util_pct ?? 0,
        ramPct: selectedRunData.hardware?.memory?.ram_usage_pct ?? 0,
        cpuPct: selectedRunData.hardware?.cpu?.usage_pct ?? 0,
      }
      const next = [...prior, sample]
      return {
        ...prev,
        [selectedRun.run_id]: next.length > MAX_SAMPLES
          ? next.slice(next.length - MAX_SAMPLES)
          : next,
      }
    })
  }, [selectedRun?.run_id, selectedRunData])

  const handleIntervalChange = (ms) => {
    setIntervalMs(ms)
    // Keep the backend poller in sync with the UI cadence.
    postConfig({ poll_interval_seconds: Math.max(ms / 1000, 0.5) }).catch(() => {})
  }

  // The engine address is owned by the backend (it follows whatever the
  // LLM-Runner last started), so it is read rather than held here.
  const queryClient = useQueryClient()
  const engineQuery = useQuery({
    queryKey: ['engine-target'],
    queryFn: fetchConfig,
  })
  const engineHost = engineQuery.data?.halogen_host ?? ''

  const handleEngineChange = async (url) => {
    // Throws on a rejected address; the caller shows the message.
    await postConfig({ halogen_host: url })
    queryClient.invalidateQueries({ queryKey: ['engine-target'] })
    queryClient.invalidateQueries({ queryKey: ['live-status'] })
  }

  const handleResetStats = () => {
    setHistory([])
    setRunHistories({})
    queryClient.invalidateQueries({ queryKey: ['live-status'] })
    queryClient.invalidateQueries({ queryKey: ['runner-live-status'] })
  }

  const connected = displayedData?.connected ?? false

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
        engineHost={engineHost}
        engineConfigName={engineQuery.data?.engine_config_name}
        onEngineChange={handleEngineChange}
      />
      <main className="mx-auto max-w-7xl px-4 py-6">
        {isError && (
          <div className="mb-4 rounded-lg border border-[var(--status-critical)] bg-[var(--surface-card)] px-4 py-2 text-sm text-[var(--status-critical)]">
            Backend unreachable. Is the dashboard API running on port 8000?
          </div>
        )}
        {tab === 'live' && (
          <ErrorBoundary>
            <LiveTab
              data={displayedData}
              history={displayedHistory}
              runs={runningRuns}
              selectedRunId={selectedRun?.run_id || ''}
              onSelectRun={setSelectedRunId}
              theme={theme}
              onResetStats={handleResetStats}
            />
          </ErrorBoundary>
        )}
        {tab === 'tuning' && (
          <ErrorBoundary>
            <TuningTab />
          </ErrorBoundary>
        )}
        {/* The chat stays mounted (hidden via CSS) so switching tabs never
            discards the conversation, the draft, or an in-flight stream.
            Its own boundary keeps a failure here from taking the other tabs
            down with it. */}
        <div className={tab === 'chat' ? '' : 'hidden'}>
          <ErrorBoundary>
            <AIChatTab active={tab === 'chat'} />
          </ErrorBoundary>
        </div>
        {/* The runner also stays mounted so a running container's stream is
            not interrupted by switching tabs. */}
        <div className={tab === 'runner' ? '' : 'hidden'}>
          <ErrorBoundary>
            <RunnerTab
              active={tab === 'runner'}
              onEngineChange={() => {
                queryClient.invalidateQueries({ queryKey: ['engine-target'] })
                queryClient.invalidateQueries({ queryKey: ['live-status'] })
              }}
            />
          </ErrorBoundary>
        </div>
      </main>
    </div>
  )
}
