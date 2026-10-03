import { useState } from 'react'
import { Info } from 'lucide-react'
import InfoModal from './InfoModal.jsx'

// Shared wrapper for every chart. When an `info` node is supplied, a small info
// button appears in the top-right corner; clicking it opens a modal explaining
// the measures shown in that chart.
export default function ChartCard({ title, subtitle, info, children, className = '' }) {
  const [showInfo, setShowInfo] = useState(false)

  return (
    <div
      className={`rounded-xl border border-[var(--border-hairline)] bg-[var(--surface-card)] p-4 ${className}`}
    >
      <div className="mb-3 flex items-start justify-between gap-2">
        <div className="min-w-0">
          <h3 className="text-sm font-semibold text-[var(--text-primary)]">{title}</h3>
          {subtitle && <p className="mt-0.5 text-xs text-[var(--text-muted)]">{subtitle}</p>}
        </div>
        {info && (
          <button
            type="button"
            onClick={() => setShowInfo(true)}
            aria-label={`About the ${title} chart`}
            title="What these measures mean"
            className="-mr-1 -mt-1 shrink-0 rounded-md p-1.5 text-[var(--text-muted)] transition-colors hover:bg-[var(--surface-page)] hover:text-[var(--text-primary)]"
          >
            <Info className="h-4 w-4" />
          </button>
        )}
      </div>
      {children}
      {showInfo && (
        <InfoModal title={title} onClose={() => setShowInfo(false)}>
          {info}
        </InfoModal>
      )}
    </div>
  )
}
