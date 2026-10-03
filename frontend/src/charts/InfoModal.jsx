import { useEffect } from 'react'
import { createPortal } from 'react-dom'
import { X } from 'lucide-react'

// Lightweight modal for chart explanations. Rendered into document.body via a
// portal so it escapes any card overflow/stacking context. Closes on backdrop
// click, the X button, or Escape.
export default function InfoModal({ title, onClose, children }) {
  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label={`About the ${title} chart`}
    >
      <div
        className="max-h-[80vh] w-full max-w-lg overflow-y-auto rounded-xl border border-[var(--border-hairline)] bg-[var(--surface-card)] p-5 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-3 flex items-start justify-between gap-3">
          <h3 className="text-base font-semibold text-[var(--text-primary)]">
            {title}
          </h3>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="shrink-0 rounded-md p-1 text-[var(--text-muted)] transition-colors hover:bg-[var(--surface-page)] hover:text-[var(--text-primary)]"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="space-y-3 text-sm leading-relaxed text-[var(--text-secondary)]">
          {children}
        </div>
      </div>
    </div>,
    document.body,
  )
}
