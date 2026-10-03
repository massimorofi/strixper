import { useQuery } from '@tanstack/react-query'
import { Cpu, Gauge, Loader2, Stethoscope } from 'lucide-react'
import { fetchSystemInfo, fetchTuningCheck } from '../api.js'
import { fmtTime } from '../format.js'
import StatusBadge from './StatusBadge.jsx'

function SummaryBanner({ info }) {
  const items = [
    { icon: Cpu, label: 'CPU', value: info?.cpu || 'AMD Zen 5 (16 Cores / 32 Threads)' },
    { icon: Gauge, label: 'GPU', value: info?.gpu || 'AMD Radeon 8060S (gfx1151)' },
    {
      icon: Gauge,
      label: 'Memory Bandwidth',
      value: info?.bandwidth || '~208 GB/s sustained',
    },
  ]
  return (
    <div className="grid grid-cols-1 gap-4 rounded-xl border border-[var(--border-hairline)] bg-[var(--surface-card)] p-4 sm:grid-cols-3">
      {items.map((it) => (
        <div key={it.label} className="flex items-start gap-3">
          <it.icon size={18} className="mt-0.5 shrink-0 text-[var(--series-1)]" />
          <div>
            <div className="text-xs font-medium uppercase tracking-wide text-[var(--text-muted)]">
              {it.label}
            </div>
            <div className="text-sm font-medium text-[var(--text-primary)]">{it.value}</div>
          </div>
        </div>
      ))}
    </div>
  )
}

export default function TuningTab() {
  const { data, isFetching, refetch } = useQuery({
    queryKey: ['tuning-check'],
    queryFn: fetchTuningCheck,
  })
  const { data: sysInfo } = useQuery({
    queryKey: ['system-info'],
    queryFn: fetchSystemInfo,
  })

  const checks = data?.checks || []
  const passCount = checks.filter((c) => c.status === 'PASS').length
  const warnCount = checks.filter((c) => c.status === 'WARN').length
  const failCount = checks.filter((c) => c.status === 'FAIL').length

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <SummaryBanner info={sysInfo} />
      </div>

      <div className="flex flex-wrap items-center gap-4">
        <button
          type="button"
          onClick={() => refetch()}
          disabled={isFetching}
          className="inline-flex items-center gap-2 rounded-md bg-[var(--series-1)] px-4 py-2 text-sm font-medium text-white transition-opacity hover:opacity-90 disabled:opacity-50"
        >
          {isFetching ? (
            <Loader2 size={16} className="animate-spin" />
          ) : (
            <Stethoscope size={16} />
          )}
          Run System Diagnostics
        </button>
        {data && (
          <div className="text-sm text-[var(--text-secondary)]">
            Evaluated {fmtTime(data.evaluated_at)} ·{' '}
            <span className="text-[var(--status-good)]">{passCount} pass</span> ·{' '}
            <span className="text-[var(--status-warning)]">{warnCount} warn</span> ·{' '}
            <span className="text-[var(--status-critical)]">{failCount} fail</span>
          </div>
        )}
      </div>

      <div className="overflow-x-auto rounded-xl border border-[var(--border-hairline)] bg-[var(--surface-card)]">
        <table className="w-full min-w-[760px] text-left text-sm">
          <thead>
            <tr className="border-b border-[var(--border-hairline)] text-xs uppercase tracking-wide text-[var(--text-muted)]">
              <th className="px-4 py-3 font-medium">Status</th>
              <th className="px-4 py-3 font-medium">Rule Checked</th>
              <th className="px-4 py-3 font-medium">Current Host Value</th>
              <th className="px-4 py-3 font-medium">Recommended Value</th>
              <th className="px-4 py-3 font-medium">Impact / Action</th>
            </tr>
          </thead>
          <tbody>
            {checks.length === 0 ? (
              <tr>
                <td colSpan={5} className="px-4 py-8 text-center text-[var(--text-muted)]">
                  No diagnostics run yet. Click "Run System Diagnostics".
                </td>
              </tr>
            ) : (
              checks.map((c) => (
                <tr
                  key={c.id}
                  className="border-b border-[var(--border-hairline)] last:border-0"
                >
                  <td className="px-4 py-3">
                    <StatusBadge status={c.status} />
                  </td>
                  <td className="px-4 py-3 font-medium text-[var(--text-primary)]">
                    {c.name}
                  </td>
                  <td className="px-4 py-3 text-[var(--text-secondary)]">
                    <span className="block max-w-[260px] truncate" title={c.actual}>
                      {c.actual}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-[var(--text-secondary)]">{c.expected}</td>
                  <td className="px-4 py-3 text-[var(--text-secondary)]">{c.impact}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
