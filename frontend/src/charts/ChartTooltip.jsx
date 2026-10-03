import { fmtNum, fmtTime } from '../format.js'

export default function ChartTooltip({ active, payload, label, unit = '', decimals = 1 }) {
  if (!active || !payload || payload.length === 0) return null
  return (
    <div className="rounded-md border border-[var(--border-hairline)] bg-[var(--surface-card)] px-3 py-2 text-xs shadow-lg">
      <div className="mb-1 text-[var(--text-muted)]">{fmtTime(label)}</div>
      {payload.map((p) => (
        <div key={p.dataKey} className="flex items-center gap-2 py-0.5">
          <span className="h-2 w-2 rounded-full" style={{ backgroundColor: p.color }} />
          <span className="text-[var(--text-secondary)]">{p.name}</span>
          <span className="font-medium tabular-nums text-[var(--text-primary)]">
            {fmtNum(p.value, decimals)}
            {unit}
          </span>
        </div>
      ))}
    </div>
  )
}
