import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
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
  downloadWorkspaceFile,
  listWorkspace,
  readWorkspaceFile,
  workspacePreviewUrl,
} from '../api.js'
import { formatBytes, isImageName } from '../utils/fileUtils.js'

function Node({ entry, depth, onOpen, onDelete }) {
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

  if (entry.is_dir) {
    return (
      <div>
        <button
          type="button"
          onClick={toggle}
          className="flex w-full items-center gap-1.5 rounded px-2 py-1 text-left text-xs text-[var(--text-secondary)] transition-colors hover:bg-[var(--surface-page)]"
          style={{ paddingLeft: `${depth * 14 + 8}px` }}
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
        {open && (
          <div>
            {isLoading ? (
              <p
                className="px-2 py-1 text-[11px] text-[var(--text-muted)]"
                style={{ paddingLeft: `${(depth + 1) * 14 + 8}px` }}
              >
                Loading…
              </p>
            ) : (
              (data?.entries || []).map((child) => (
                <Node
                  key={child.path}
                  entry={child}
                  depth={depth + 1}
                  onOpen={onOpen}
                  onDelete={onDelete}
                />
              ))
            )}
            {(data?.entries || []).length === 0 && !isLoading && (
              <p
                className="py-1 text-[11px] text-[var(--text-muted)]"
                style={{ paddingLeft: `${(depth + 1) * 14 + 8}px` }}
              >
                empty
              </p>
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
      <span className="w-3 shrink-0" />
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
          className="shrink-0 rounded p-0.5 text-[var(--text-muted)] opacity-0 transition-opacity hover:text-[var(--series-1)] group-hover:opacity-100"
        >
          <Eye size={12} />
        </button>
      )}
      <button
        type="button"
        onClick={() => downloadWorkspaceFile(entry.path, entry.name)}
        aria-label={`Download ${entry.name}`}
        className="shrink-0 rounded p-0.5 text-[var(--text-muted)] opacity-0 transition-opacity hover:text-[var(--series-1)] group-hover:opacity-100"
      >
        <Download size={12} />
      </button>
      <button
        type="button"
        onClick={() => onDelete(entry)}
        aria-label={`Delete ${entry.name}`}
        className="shrink-0 rounded p-0.5 text-[var(--text-muted)] opacity-0 transition-opacity hover:text-[var(--status-critical)] group-hover:opacity-100"
      >
        <Trash2 size={12} />
      </button>
    </div>
  )
}

export default function WorkspaceFileTree({ active = true }) {
  const [viewing, setViewing] = useState(null)
  const [error, setError] = useState(null)
  const { data, isLoading, refetch } = useQuery({
    queryKey: ['workspace-root'],
    queryFn: () => listWorkspace('.'),
    enabled: active,
  })

  const open = async (entry) => {
    setError(null)
    if (isImageName(entry.name)) {
      setViewing({ kind: 'image', path: entry.path, name: entry.name })
      return
    }
    try {
      const res = await readWorkspaceFile(entry.path)
      setViewing({
        kind: 'text',
        path: entry.path,
        name: entry.name,
        content: res.content,
        truncated: res.truncated,
      })
    } catch (e) {
      setError(e?.message || 'Could not read file')
    }
  }

  const remove = async (entry) => {
    if (!window.confirm(`Delete ${entry.path} from the workspace?`)) return
    setError(null)
    try {
      await deleteWorkspacePath(entry.path)
      refetch()
    } catch (e) {
      setError(e?.message || 'Delete failed')
    }
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2">
      <div className="flex shrink-0 items-center gap-2">
        <h3 className="text-sm font-semibold text-[var(--text-primary)]">
          Workspace
        </h3>
        <span className="text-xs text-[var(--text-muted)]">
          files the agent can read and write
        </span>
        <button
          type="button"
          onClick={() => refetch()}
          className="ml-auto rounded-md border border-[var(--border-hairline)] p-1.5 text-[var(--text-secondary)] transition-colors hover:bg-[var(--surface-page)]"
          aria-label="Refresh workspace listing"
        >
          <RefreshCw size={13} />
        </button>
      </div>

      {error && (
        <p className="shrink-0 rounded-lg border border-[var(--status-critical)] px-3 py-1.5 text-xs text-[var(--status-critical)]">
          {error}
        </p>
      )}

      <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 md:grid-cols-2">
        <div className="min-h-0 overflow-auto rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-card)] p-1">
          {isLoading ? (
            <p className="px-2 py-2 text-xs text-[var(--text-muted)]">Loading…</p>
          ) : (data?.entries || []).length === 0 ? (
            <p className="px-2 py-2 text-xs text-[var(--text-muted)]">
              The workspace is empty.
            </p>
          ) : (
            (data?.entries || []).map((entry) => (
              <Node
                key={entry.path}
                entry={entry}
                depth={0}
                onOpen={open}
                onDelete={remove}
              />
            ))
          )}
        </div>

        <div className="min-h-0 overflow-auto rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-card)]">
          {!viewing ? (
            <p className="px-3 py-3 text-xs text-[var(--text-muted)]">
              Select a file to preview it.
            </p>
          ) : (
            <div className="flex h-full flex-col">
              <div className="flex shrink-0 items-center gap-2 border-b border-[var(--border-hairline)] px-3 py-1.5">
                <span className="truncate text-xs font-medium text-[var(--text-primary)]">
                  {viewing.name}
                </span>
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
                <div className="flex flex-1 items-center justify-center p-3">
                  <img
                    src={workspacePreviewUrl(viewing.path)}
                    alt={viewing.name}
                    className="max-h-full max-w-full rounded object-contain"
                  />
                </div>
              ) : (
                <pre className="flex-1 overflow-auto whitespace-pre-wrap break-words px-3 py-2 font-mono text-[11px] leading-relaxed text-[var(--text-primary)]">
                  {viewing.content}
                </pre>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
