import { useState } from 'react'
import { Download, FileText, X } from 'lucide-react'
import { downloadFile } from '../api.js'
import { formatBytes, isImageName } from '../utils/fileUtils.js'

// Attachments rendered inside a sent message.
//
// Images use the backend preview URL directly (the browser caches it and
// it needs no JS), and clicking one opens a full-size overlay. Other
// files are chips with a download button.
export default function MessageAttachments({ attachments }) {
  const [lightbox, setLightbox] = useState(null)
  const [busyId, setBusyId] = useState(null)
  const [err, setErr] = useState(null)

  if (!attachments || attachments.length === 0) return null

  const images = attachments.filter((a) => isImageName(a.name) || (a.mime_type || '').startsWith('image/'))
  const others = attachments.filter((a) => !images.includes(a))

  const grab = async (a) => {
    setBusyId(a.file_id)
    setErr(null)
    try {
      await downloadFile(a.file_id, a.name)
    } catch (e) {
      setErr(e?.message || 'Download failed')
    } finally {
      setBusyId(null)
    }
  }

  return (
    <div className="mt-2 space-y-2">
      {images.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {images.map((a) => (
            <button
              key={a.file_id || a.name}
              type="button"
              onClick={() => setLightbox(a)}
              className="group relative overflow-hidden rounded-lg border border-[var(--border-hairline)] transition-transform hover:scale-[1.02]"
              title={a.name}
            >
              <img
                src={`/api/v1/files/${encodeURIComponent(a.file_id)}/preview`}
                alt={a.name}
                loading="lazy"
                className="h-24 w-24 object-cover"
              />
            </button>
          ))}
        </div>
      )}

      {others.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {others.map((a) => (
            <div
              key={a.file_id || a.name}
              className="flex items-center gap-2 rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-page)] px-2 py-1.5 text-xs"
            >
              <FileText size={14} className="text-[var(--text-muted)]" />
              <span className="max-w-48 truncate text-[var(--text-secondary)]">
                {a.name}
              </span>
              <span className="text-[var(--text-muted)]">
                {formatBytes(a.size)}
              </span>
              <button
                type="button"
                onClick={() => grab(a)}
                disabled={busyId === a.file_id}
                aria-label={`Download ${a.name}`}
                className="rounded p-0.5 text-[var(--text-muted)] transition-colors hover:bg-[var(--surface-card)] hover:text-[var(--series-1)] disabled:opacity-50"
              >
                <Download size={13} />
              </button>
            </div>
          ))}
        </div>
      )}

      {err && (
        <p className="text-[11px] text-[var(--status-critical)]">{err}</p>
      )}

      {lightbox && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-6"
          onClick={() => setLightbox(null)}
          role="dialog"
          aria-modal="true"
          aria-label={lightbox.name}
        >
          <div
            className="relative max-h-full max-w-full"
            onClick={(e) => e.stopPropagation()}
          >
            <img
              src={`/api/v1/files/${encodeURIComponent(lightbox.file_id)}/preview`}
              alt={lightbox.name}
              className="max-h-[85vh] max-w-full rounded-lg border border-[var(--border-hairline)] object-contain"
            />
            <div className="mt-2 flex items-center justify-between gap-3 text-xs text-[var(--text-secondary)]">
              <span className="truncate">{lightbox.name}</span>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => grab(lightbox)}
                  className="inline-flex items-center gap-1 rounded-md border border-[var(--border-hairline)] px-2 py-1 transition-colors hover:bg-[var(--surface-card)]"
                >
                  <Download size={12} /> Download
                </button>
                <button
                  type="button"
                  onClick={() => setLightbox(null)}
                  aria-label="Close"
                  className="rounded-md border border-[var(--border-hairline)] p-1 transition-colors hover:bg-[var(--surface-card)]"
                >
                  <X size={13} />
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
