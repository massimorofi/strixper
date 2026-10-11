import { useCallback, useRef, useState } from 'react'
import { FileUp, ImagePlus, Loader2, X } from 'lucide-react'
import { formatBytes } from '../utils/fileUtils.js'

// Drag-and-drop / click-to-pick zone for chat attachments.
//
// The zone is deliberately unobtrusive: it sits above the composer and
// only expands its border styling while a drag is actually over it.
// Uploads start immediately on selection, so by the time the user finishes
// typing the files are usually already stored.
export default function FileUploadZone({
  items = [],
  onAdd,
  onRemove,
  onRetry,
  disabled = false,
  accept = 'image/*,application/pdf,text/*',
}) {
  const inputRef = useRef(null)
  const [dragging, setDragging] = useState(false)
  const depth = useRef(0)

  const pick = useCallback(() => {
    if (disabled) return
    inputRef.current?.click()
  }, [disabled])

  const handleDrop = useCallback(
    (e) => {
      e.preventDefault()
      depth.current = 0
      setDragging(false)
      if (disabled) return
      const files = e.dataTransfer?.files
      if (files && files.length > 0) onAdd?.(files)
    },
    [disabled, onAdd],
  )

  const handleChange = useCallback(
    (e) => {
      const files = e.target?.files
      if (files && files.length > 0) onAdd?.(files)
      // Reset so picking the same file twice still fires change.
      e.target.value = ''
    },
    [onAdd],
  )

  return (
    <div className="mb-2">
      <input
        ref={inputRef}
        type="file"
        multiple
        accept={accept}
        onChange={handleChange}
        className="hidden"
        aria-label="Attach files"
      />

      {items.length > 0 && (
        <div className="mb-2 flex flex-wrap gap-2">
          {items.map((it) => (
            <div
              key={it.key}
              className={`relative flex items-center gap-2 rounded-lg border px-2 py-1.5 text-xs ${
                it.status === 'error'
                  ? 'border-[var(--status-critical)] bg-[var(--surface-page)]'
                  : 'border-[var(--border-hairline)] bg-[var(--surface-page)]'
              }`}
              title={it.error || `${it.name} · ${formatBytes(it.size)}`}
            >
              {it.isImage && it.previewUrl ? (
                <img
                  src={it.previewUrl}
                  alt={it.name}
                  className="h-10 w-10 rounded object-cover"
                />
              ) : (
                <span className="flex h-10 w-10 items-center justify-center rounded bg-[var(--surface-card)] text-[var(--text-muted)]">
                  <FileUp size={15} />
                </span>
              )}
              <span className="max-w-40 truncate text-[var(--text-secondary)]">
                {it.name}
              </span>
              <span className="text-[var(--text-muted)]">
                {formatBytes(it.size)}
              </span>
              {it.status === 'uploading' && (
                <Loader2
                  size={13}
                  className="animate-spin text-[var(--series-1)]"
                />
              )}
              {it.status === 'error' && (
                <button
                  type="button"
                  onClick={() => onRetry?.(it.key)}
                  className="rounded px-1.5 py-0.5 text-[11px] text-[var(--series-1)] hover:bg-[var(--surface-card)]"
                >
                  Retry
                </button>
              )}
              <button
                type="button"
                onClick={() => onRemove?.(it.key)}
                aria-label={`Remove ${it.name}`}
                className="rounded p-0.5 text-[var(--text-muted)] transition-colors hover:bg-[var(--surface-card)] hover:text-[var(--text-primary)]"
              >
                <X size={13} />
              </button>
            </div>
          ))}
        </div>
      )}

      <div
        role="button"
        tabIndex={0}
        aria-disabled={disabled}
        onClick={pick}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault()
            pick()
          }
        }}
        onDragEnter={(e) => {
          e.preventDefault()
          depth.current += 1
          if (!disabled) setDragging(true)
        }}
        onDragOver={(e) => e.preventDefault()}
        onDragLeave={(e) => {
          e.preventDefault()
          depth.current -= 1
          if (depth.current <= 0) {
            depth.current = 0
            setDragging(false)
          }
        }}
        onDrop={handleDrop}
        className={`flex cursor-pointer items-center justify-center gap-1.5 rounded-lg border border-dashed px-3 py-2 text-xs transition-colors ${
          dragging
            ? 'border-[var(--series-1)] bg-[var(--surface-page)] text-[var(--series-1)]'
            : 'border-[var(--border-hairline)] text-[var(--text-muted)] hover:border-[var(--series-1)] hover:text-[var(--text-secondary)]'
        } ${disabled ? 'cursor-not-allowed opacity-50' : ''}`}
      >
        {items.length === 0 ? (
          <ImagePlus size={13} />
        ) : (
          <FileUp size={13} />
        )}
        <span>Drop files here, or click to browse</span>
      </div>
    </div>
  )
}
