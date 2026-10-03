import { useState } from 'react'
import { Info } from 'lucide-react'
import InfoModal from '../charts/InfoModal.jsx'

// A single KPI card. When an `info` node is supplied, a small info button appears
// in the top-right corner; clicking it opens a modal explaining the measure.
export default function StatTile({ label, value, sub, info }) {
  const [showInfo, setShowInfo] = useState(false)

  return (
    <div className="rounded-xl border border-[var(--border-hairline)] bg-[var(--surface-card)] p-4">
      <div className="flex items-start justify-between gap-2">
        <div className="text-xs font-medium uppercase tracking-wide text-[var(--text-muted)]">
          {label}
        </div>
        {info && (
          <button
            type="button"
            onClick={() => setShowInfo(true)}
            aria-label={`About ${label}`}
            title="What this measure means"
            className="-mr-1 -mt-1 shrink-0 rounded-md p-1 text-[var(--text-muted)] transition-colors hover:bg-[var(--surface-page)] hover:text-[var(--text-primary)]"
          >
            <Info className="h-3.5 w-3.5" />
          </button>
        )}
      </div>
      <div className="mt-1 text-2xl font-semibold text-[var(--text-primary)]">{value}</div>
      {sub != null && (
        <div className="mt-1 text-sm text-[var(--text-secondary)]">{sub}</div>
      )}
      {showInfo && (
        <InfoModal title={label} onClose={() => setShowInfo(false)}>
          {info}
        </InfoModal>
      )}
    </div>
  )
}
