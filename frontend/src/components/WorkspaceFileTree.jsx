import { useCallback, useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  CheckSquare,
  ChevronDown,
  ChevronRight,
  Download,
  Eye,
  FileText,
  Folder,
  FolderOpen,
  RefreshCw,
  Trash2,
  X,
} from 'lucide-react'
import {
  deleteWorkspacePath,
  deleteWorkspacePaths,
  downloadWorkspaceFile,
  listWorkspace,
  readWorkspaceFile,
  workspacePreviewUrl,
} from '../api.js'
import { formatBytes, isImageName } from '../utils/fileUtils.js'
import { highlightCode, languageForFile, languageLabel } from '../utils/highlight.js'

// Paths of every descendant in a *loaded* listing. The tree is lazily
// loaded, so this only sees directories the user has expanded -- fine,
// because the backend deletes recursively and the cascade is a visual
// convenience, not the delete plan.
function collectPaths(entries) {
  const out = []
  const walk = (list) => {
    for (const e of list || []) {
      out.push(e.path)
      if (e.is_dir && e.children) walk(e.children)
    }
  }
  walk(entries)
  return out
}

function NodeCheckbox({ checked, onChange, label }) {
  return (
    <input
      type="checkbox"
      checked={checked}
      onChange={(e) => {
        e.stopPropagation()
        onChange(e.target.checked)
      }}
      onClick={(e) => e.stopPropagation()}
      aria-label={label}
      className="h-3.5 w-3.5 shrink-0 cursor-pointer accent-[var(--series-1)]"
    />
  )
}

function Node({ entry, depth, onOpen, onDelete, selection, toggleNode }) {
  const [open, setOpen] = useState(false)
  const { data, isLoading, refetch } = useQuery({
    queryKey: ['workspace', entry.path],
    queryFn: () => listWorkspace(entry.path),
    enabled: entry.is_dir && open,
  })

  const toggle = () => {
    if (!entry.is_dir) return
    setOpen((o) => !o)
    if (!open) refetch()
  }

  const checked = selection.has(entry.path)

  if (entry.is_dir) {
    const children = data?.entries || []
    return (
      <div>
        <div
          className="group flex items-center gap-1.5 rounded px-2 py-1 text-xs text-[var(--text-secondary)] transition-colors hover:bg-[var(--surface-page)]"
          style={{ paddingLeft: `${depth * 14 + 8}px` }}
        >
          <NodeCheckbox
            checked={checked}
            label={`Select ${entry.name}`}
            onChange={(next) => toggleNode(entry.path, next)}
          />
          <button
            type="button"
            onClick={toggle}
            className="flex min-w-0 flex-1 items-center gap-1.5 text-left transition-colors hover:text-[var(--series-1)]"
            aria-expanded={open}
          >
            {open ? (
              <ChevronDown size={13} className="shrink-0 text-[var(--text-muted)]" />
            ) : (
              <ChevronRight size={13} className="shrink-0 text-[var(--text-muted)]" />
            )}
            {open ? (
              <FolderOpen size={13} className="shrink-0 text-[var(--series-1)]" />
            ) : (
              <Folder size={13} className="shrink-0 text-[var(--text-muted)]" />
            )}
            <span className="truncate">{entry.name}</span>
          </button>
          <button
            type="button"
            onClick={() => onDelete(entry)}
            aria-label={`Delete ${entry.name}`}
            className="shrink-0 rounded p-0.5 text-[var(--text-muted)] opacity-0 transition-opacity hover:text-[var(--status-critical)] focus:opacity-100 group-hover:opacity-100"
          >
            <Trash2 size={12} />
          </button>
        </div>
        {open && (
          <div>
            {isLoading ? (
              <p
                className="px-2 py-1 text-[11px] text-[var(--text-muted)]"
                style={{ paddingLeft: `${(depth + 1) * 14 + 8}px` }}
              >
                Loading…
              </p>
            ) : children.length === 0 ? (
              <p
                className="py-1 text-[11px] text-[var(--text-muted)]"
                style={{ paddingLeft: `${(depth + 1) * 14 + 8}px` }}
              >
                empty
              </p>
            ) : (
              children.map((child) => (
                <Node
                  key={child.path}
                  entry={child}
                  depth={depth + 1}
                  onOpen={onOpen}
                  onDelete={onDelete}
                  selection={selection}
                  toggleNode={toggleNode}
                />
              ))
            )}
          </div>
        )}
      </div>
    )
  }

  const image = isImageName(entry.name)
  return (
    <div
      className="group flex items-center gap-1.5 rounded px-2 py-1 text-xs text-[var(--text-secondary)] hover:bg-[var(--surface-page)]"
      style={{ paddingLeft: `${depth * 14 + 8}px` }}
    >
      <NodeCheckbox
        checked={checked}
        label={`Select ${entry.name}`}
        onChange={(next) => toggleNode(entry.path, next)}
      />
      <FileText size={13} className="shrink-0 text-[var(--text-muted)]" />
      <button
        type="button"
        onClick={() => onOpen(entry)}
        className="min-w-0 flex-1 truncate text-left transition-colors hover:text-[var(--series-1)]"
        title={entry.path}
      >
        {entry.name}
      </button>
      <span className="shrink-0 text-[11px] text-[var(--text-muted)]">
        {formatBytes(entry.size)}
      </span>
      {image && (
        <button
          type="button"
          onClick={() => onOpen(entry)}
          aria-label={`Preview ${entry.name}`}
          className="shrink-0 rounded p-0.5 text-[var(--text-muted)] opacity-0 transition-opacity hover:text-[var(--series-1)] focus:opacity-100 group-hover:opacity-100"
        >
          <Eye size={12} />
        </button>
      )}
      <button
        type="button"
        onClick={() => downloadWorkspaceFile(entry.path, entry.name)}
        aria-label={`Download ${entry.name}`}
        className="shrink-0 rounded p-0.5 text-[var(--text-muted)] opacity-0 transition-opacity hover:text-[var(--series-1)] focus:opacity-100 group-hover:opacity-100"
      >
        <Download size={12} />
      </button>
      <button
        type="button"
        onClick={() => onDelete(entry)}
        aria-label={`Delete ${entry.name}`}
        className="shrink-0 rounded p-0.5 text-[var(--text-muted)] opacity-0 transition-opacity hover:text-[var(--status-critical)] focus:opacity-100 group-hover:opacity-100"
      >
        <Trash2 size={12} />
      </button>
    </div>
  )
}

export default function WorkspaceFileTree({ active = true }) {
  const queryClient = useQueryClient()
  const [viewing, setViewing] = useState(null)
  const [error, setError] = useState(null)
  const [selection, setSelection] = useState(() => new Set())
  const [bulkBusy, setBulkBusy] = useState(false)
  const { data, isLoading, refetch } = useQuery({
    queryKey: ['workspace-root'],
    queryFn: () => listWorkspace('.'),
    enabled: active,
  })

  // After any delete, drop every cached directory listing (root + all
  // lazily-loaded subdirectories) so the tree reflects the new state.
  const refreshAll = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ['workspace'] })
    queryClient.invalidateQueries({ queryKey: ['workspace-root'] })
    refetch()
  }, [queryClient, refetch])

  const rootEntries = useMemo(() => data?.entries || [], [data])
  const rootPaths = useMemo(() => collectPaths(rootEntries), [rootEntries])
  const selectedCount = selection.size

  const toggleNode = useCallback((path, next) => {
    setSelection((prev) => {
      const out = new Set(prev)
      if (next) out.add(path)
      else out.delete(path)
      return out
    })
  }, [])

  const selectAll = () => setSelection(new Set(rootPaths))
  const clearSelection = () => setSelection(new Set())

  const open = async (entry) => {
    setError(null)
    if (isImageName(entry.name)) {
      setViewing({ kind: 'image', path: entry.path, name: entry.name })
      return
    }
    try {
      const res = await readWorkspaceFile(entry.path)
      const lang = languageForFile(entry.name)
      setViewing({
        kind: 'text',
        path: entry.path,
        name: entry.name,
        content: res.content,
        truncated: res.truncated,
        lang,
        html: highlightCode(res.content, lang),
      })
    } catch (e) {
      setError(e?.message || 'Could not read file')
    }
  }

  const remove = async (entry) => {
    const what = entry.is_dir
      ? `the directory ${entry.path} and everything inside it`
      : entry.path
    if (!window.confirm(`Delete ${what} from the workspace?`)) return
    setError(null)
    try {
      await deleteWorkspacePath(entry.path, { recursive: entry.is_dir })
      setSelection((prev) => {
        const out = new Set(prev)
        out.delete(entry.path)
        return out
      })
      if (viewing && viewing.path === entry.path) setViewing(null)
      refreshAll()
    } catch (e) {
      setError(e?.message || 'Delete failed')
    }
  }

  const deleteSelected = async () => {
    const paths = Array.from(selection)
    if (!paths.length) return
    if (
      !window.confirm(
        `Delete ${paths.length} item${paths.length === 1 ? '' : 's'} from the workspace? This cannot be undone.`,
      )
    ) {
      return
    }
    setBulkBusy(true)
    setError(null)
    try {
      const res = await deleteWorkspacePaths(paths, { recursive: true })
      const failed = res?.failed || []
      setSelection(new Set(failed.map((f) => f.path)))
      if (viewing && paths.includes(viewing.path)) setViewing(null)
      if (failed.length) {
        setError(
          `${res.deleted_count} deleted, ${failed.length} failed: ${failed[0]?.error || 'unknown error'}`,
        )
      }
      refreshAll()
    } catch (e) {
      setError(e?.message || 'Bulk delete failed')
    } finally {
      setBulkBusy(false)
    }
  }

  return (
    <div className="flex h-full min-h-0 flex-col gap-2">
      <div className="flex shrink-0 flex-wrap items-center gap-2">
        <h3 className="text-sm font-semibold text-[var(--text-primary)]">
          Workspace
        </h3>
        <span className="text-xs text-[var(--text-muted)]">
          files the agent can read and write
        </span>

        <div className="ml-auto flex items-center gap-2">
          {selectedCount > 0 ? (
            <>
              <span className="text-xs font-medium text-[var(--series-1)]">
                {selectedCount} selected
              </span>
              <button
                type="button"
                onClick={clearSelection}
                className="rounded-md border border-[var(--border-hairline)] px-2 py-1 text-[11px] text-[var(--text-secondary)] transition-colors hover:bg-[var(--surface-page)]"
              >
                Clear
              </button>
              <button
                type="button"
                onClick={deleteSelected}
                disabled={bulkBusy}
                className="flex items-center gap-1 rounded-md border border-[var(--status-critical)] px-2 py-1 text-[11px] font-medium text-[var(--status-critical)] transition-colors hover:bg-[var(--status-critical)] hover:text-white disabled:opacity-50"
              >
                <Trash2 size={12} />
                {bulkBusy ? 'Deleting…' : 'Delete'}
              </button>
            </>
          ) : (
            <button
              type="button"
              onClick={selectAll}
              className="flex items-center gap-1 rounded-md border border-[var(--border-hairline)] px-2 py-1 text-[11px] text-[var(--text-secondary)] transition-colors hover:bg-[var(--surface-page)]"
            >
              <CheckSquare size={12} />
              Select all
            </button>
          )}
          <button
            type="button"
            onClick={() => {
              clearSelection()
              refreshAll()
            }}
            className="rounded-md border border-[var(--border-hairline)] p-1.5 text-[var(--text-secondary)] transition-colors hover:bg-[var(--surface-page)]"
            aria-label="Refresh workspace listing"
          >
            <RefreshCw size={13} />
          </button>
        </div>
      </div>

      {error && (
        <p className="shrink-0 rounded-lg border border-[var(--status-critical)] px-3 py-1.5 text-xs text-[var(--status-critical)]">
          {error}
        </p>
      )}

      <div className="flex min-h-0 flex-1 flex-col gap-3 md:flex-row">
        <div className="workspace-scroll min-h-0 flex-1 overflow-y-auto overscroll-contain rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-card)] p-1 md:min-w-0">
          {isLoading ? (
            <p className="px-2 py-2 text-xs text-[var(--text-muted)]">Loading…</p>
          ) : rootEntries.length === 0 ? (
            <p className="px-2 py-2 text-xs text-[var(--text-muted)]">
              The workspace is empty.
            </p>
          ) : (
            rootEntries.map((entry) => (
              <Node
                key={entry.path}
                entry={entry}
                depth={0}
                onOpen={open}
                onDelete={remove}
                selection={selection}
                toggleNode={toggleNode}
              />
            ))
          )}
        </div>

        <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-card)] md:min-w-0">
          {!viewing ? (
            <p className="px-3 py-3 text-xs text-[var(--text-muted)]">
              Select a file to preview it.
            </p>
          ) : (
            <div className="flex h-full min-h-0 flex-col">
              <div className="flex shrink-0 items-center gap-2 border-b border-[var(--border-hairline)] px-3 py-1.5">
                <span className="truncate text-xs font-medium text-[var(--text-primary)]">
                  {viewing.name}
                </span>
                {viewing.kind === 'text' ? (
                  <span className="shrink-0 rounded bg-[var(--surface-page)] px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-[var(--text-muted)]">
                    {languageLabel(viewing.lang)}
                  </span>
                ) : null}
                {viewing.truncated ? (
                  <span className="shrink-0 text-[11px] text-[var(--status-warning)]">
                    truncated
                  </span>
                ) : null}
                <button
                  type="button"
                  onClick={() => setViewing(null)}
                  className="ml-auto shrink-0 rounded p-0.5 text-[var(--text-muted)] hover:text-[var(--text-primary)]"
                  aria-label="Close preview"
                >
                  <X size={13} />
                </button>
              </div>
              {viewing.kind === 'image' ? (
                <div className="workspace-scroll flex flex-1 items-center justify-center overflow-auto p-3">
                  <img
                    src={workspacePreviewUrl(viewing.path)}
                    alt={viewing.name}
                    className="max-h-full max-w-full rounded object-contain"
                  />
                </div>
              ) : (
                <pre className="workspace-scroll hljs min-h-0 flex-1 overflow-auto whitespace-pre-wrap break-words px-3 py-2 font-mono text-[11px] leading-relaxed">
                  <code
                    className={`language-${viewing.lang}`}
                    dangerouslySetInnerHTML={{ __html: viewing.html }}
                  />
                </pre>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
