import { useState } from 'react'
import { RotateCcw } from 'lucide-react'
import { fmtNum } from '../format.js'
import { resetStats } from '../api.js'

const ROWS = [
  { label: 'Prompt tokens/s', key: 'prompt_tps', unit: 't/s', digits: 0 },
  { label: 'Decode tokens/s', key: 'decode_tps', unit: 't/s', digits: 1 },
  { label: 'GPU memory (GTT)', key: 'gtt_used_gb', unit: 'GB', digits: 1 },
  { label: 'GPU usage (GTT)', key: 'gtt_pct', unit: '%', digits: 1 },
  { label: 'VRAM usage', key: 'vram_pct', unit: '%', digits: 1 },
  { label: 'GPU utilization', key: 'gpu_util_pct', unit: '%', digits: 1 },
  { label: 'CPU utilization', key: 'cpu_pct', unit: '%', digits: 1 },
  { label: 'Draft acceptance', key: 'draft_acceptance', unit: '%', digits: 1 },
  { label: 'Cache hit rate', key: 'cache_hit_rate', unit: '%', digits: 1 },
  { label: 'Cache token hit rate', key: 'cache_token_hit_rate', unit: '%', digits: 1 },
]

function fmtSince(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleString('en-GB', {
    year: 'numeric',
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

export default function StatsSummary({ stats, onReset }) {
  const [resetting, setResetting] = useState(false)
  const [resetError, setResetError] = useState('')

  const handleReset = async () => {
    if (!window.confirm('Reset all session statistics back to zero?')) return
    setResetting(true)
    setResetError('')
    try {
      await resetStats()
      onReset?.()
    } catch (err) {
      setResetError(err?.message || 'Failed to reset session statistics')
    } finally {
      setResetting(false)
    }
  }

  const samples = stats?.samples ?? 0
  const startedAt = fmtSince(stats?.started_at)

  return (
    <div className="rounded-xl border border-[var(--border-hairline)] bg-[var(--surface-card)] p-4">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-[var(--text-primary)]">
            Session Statistics
          </h2>
          <p className="mt-0.5 text-xs text-[var(--text-muted)]">
            since {startedAt || 'startup'} · {samples} sample{samples === 1 ? '' : 's'}
          </p>
        </div>
        <button
          type="button"
          onClick={handleReset}
          disabled={resetting}
          className="inline-flex items-center gap-1.5 rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-page)] px-3 py-1.5 text-xs font-medium text-[var(--text-secondary)] transition-colors hover:border-[var(--series-1)] hover:text-[var(--text-primary)] disabled:cursor-not-allowed disabled:opacity-50"
        >
          <RotateCcw className="h-3.5 w-3.5" />
          {resetting ? 'Resetting…' : 'Reset statistics'}
        </button>
      </div>
      {resetError && (
        <p className="mt-2 text-xs text-[var(--status-critical)]" role="alert">
          {resetError}
        </p>
      )}

      <div className="mt-3 overflow-x-auto">
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr className="text-xs uppercase tracking-wide text-[var(--text-muted)]">
              <th className="pb-2 text-left font-medium">Metric</th>
              <th className="pb-2 text-right font-medium">Min</th>
              <th className="pb-2 text-right font-medium">Avg</th>
              <th className="pb-2 text-right font-medium">Max</th>
            </tr>
          </thead>
          <tbody>
            {ROWS.map((row) => {
              const s = stats?.[row.key] ?? { min: 0, avg: 0, max: 0 }
              return (
                <tr
                  key={row.key}
                  className="border-t border-[var(--border-hairline)]"
                >
                  <td className="py-2 pr-4 text-left text-[var(--text-secondary)]">
                    {row.label}
                  </td>
                  {['min', 'avg', 'max'].map((c) => (
                    <td
                      key={c}
                      className="py-2 pl-4 text-right tabular-nums text-[var(--text-primary)]"
                    >
                      {fmtNum(s[c], row.digits)}
                      <span className="ml-0.5 text-[var(--text-muted)]">
                        {row.unit}
                      </span>
                    </td>
                  ))}
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
