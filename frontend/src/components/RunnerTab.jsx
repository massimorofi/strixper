import { useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Loader2,
  Pencil,
  Play,
  Plus,
  Square,
  Terminal,
  Trash2,
  X,
} from 'lucide-react'
import {
  createRunnerConfig,
  deleteRunnerConfig,
  fetchRunnerConfigs,
  stopRunnerRun,
  streamRunnerRun,
  updateRunnerConfig,
} from '../api.js'
import { fmtTime } from '../format.js'

const EMPTY_FORM = { name: '', docker_command: '', description: '' }

function ConfigForm({ initial, saving, error, onSubmit, onCancel }) {
  const [form, setForm] = useState(initial || EMPTY_FORM)
  const isEdit = Boolean(initial?.id)

  useEffect(() => {
    setForm(initial || EMPTY_FORM)
  }, [initial])

  const set = (key) => (e) => setForm((f) => ({ ...f, [key]: e.target.value }))

  const canSubmit =
    form.name.trim() && form.docker_command.trim() && !saving

  return (
    <div className="rounded-xl border border-[var(--border-hairline)] bg-[var(--surface-card)] p-4">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-sm font-semibold text-[var(--text-primary)]">
          {isEdit ? 'Edit configuration' : 'New configuration'}
        </h3>
        {isEdit && (
          <button
            type="button"
            onClick={onCancel}
            title="Cancel editing"
            className="rounded-md border border-[var(--border-hairline)] p-1 text-[var(--text-secondary)] transition-colors hover:bg-[var(--surface-page)] hover:text-[var(--text-primary)]"
          >
            <X size={14} />
          </button>
        )}
      </div>

      <div className="space-y-3">
        <label className="block">
          <span className="mb-1 block text-xs font-medium uppercase tracking-wide text-[var(--text-muted)]">
            Name
          </span>
          <input
            type="text"
            value={form.name}
            onChange={set('name')}
            maxLength={120}
            placeholder="e.g. Qwen3 local inference"
            className="w-full rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-page)] px-3 py-2 text-sm text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:border-[var(--series-1)] focus:outline-none"
          />
        </label>

        <label className="block">
          <span className="mb-1 block text-xs font-medium uppercase tracking-wide text-[var(--text-muted)]">
            Docker command
          </span>
          <textarea
            value={form.docker_command}
            onChange={set('docker_command')}
            rows={4}
            spellCheck={false}
            placeholder={'docker run --rm -it --device /dev/kfd ... my-llm-image'}
            className="w-full resize-y rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-page)] px-3 py-2 font-mono text-xs text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:border-[var(--series-1)] focus:outline-none"
          />
        </label>

        <label className="block">
          <span className="mb-1 block text-xs font-medium uppercase tracking-wide text-[var(--text-muted)]">
            Description (optional)
          </span>
          <input
            type="text"
            value={form.description}
            onChange={set('description')}
            maxLength={1000}
            placeholder="What this run does"
            className="w-full rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-page)] px-3 py-2 text-sm text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:border-[var(--series-1)] focus:outline-none"
          />
        </label>

        {error && (
          <div className="rounded-lg border border-[var(--status-critical)] px-3 py-1.5 text-xs text-[var(--status-critical)]">
            {error}
          </div>
        )}

        <div className="flex items-center gap-2 pt-1">
          <button
            type="button"
            disabled={!canSubmit}
            onClick={() => onSubmit(form)}
            className="inline-flex items-center gap-1.5 rounded-md bg-[var(--series-1)] px-4 py-2 text-sm font-medium text-white transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {saving ? (
              <Loader2 size={14} className="animate-spin" />
            ) : (
              <Plus size={14} />
            )}
            {isEdit ? 'Save changes' : 'Create configuration'}
          </button>
          {isEdit && (
            <button
              type="button"
              onClick={onCancel}
              className="rounded-md border border-[var(--border-hairline)] px-4 py-2 text-sm text-[var(--text-secondary)] transition-colors hover:bg-[var(--surface-page)] hover:text-[var(--text-primary)]"
            >
              Cancel
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

function ConfigList({ configs, runningId, onSelectRun, onEdit, onDelete }) {
  if (!configs.length) {
    return (
      <div className="flex h-full min-h-[120px] flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-[var(--border-hairline)] p-6 text-center">
        <Terminal size={22} className="text-[var(--text-muted)]" />
        <p className="text-sm text-[var(--text-secondary)]">
          No configurations yet.
        </p>
        <p className="max-w-xs text-xs text-[var(--text-muted)]">
          Create a configuration with the docker command used to launch your LLM
          inference container.
        </p>
      </div>
    )
  }
  return (
    <ul className="space-y-2">
      {configs.map((c) => {
        const isRunning = runningId === c.id
        return (
          <li
            key={c.id}
            className="rounded-xl border border-[var(--border-hairline)] bg-[var(--surface-card)] p-3"
          >
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className="truncate text-sm font-semibold text-[var(--text-primary)]">
                    {c.name}
                  </span>
                  {isRunning && (
                    <span className="inline-flex items-center gap-1 rounded-full bg-[var(--status-good)]/15 px-2 py-0.5 text-[10px] font-medium text-[var(--status-good)]">
                      <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-[var(--status-good)]" />
                      RUNNING
                    </span>
                  )}
                </div>
                {c.description && (
                  <p className="mt-0.5 truncate text-xs text-[var(--text-secondary)]">
                    {c.description}
                  </p>
                )}
                <p
                  className="mt-1 truncate font-mono text-[11px] text-[var(--text-muted)]"
                  title={c.docker_command}
                >
                  {c.docker_command}
                </p>
              </div>
            </div>
            <div className="mt-2 flex items-center gap-2">
              <button
                type="button"
                disabled={isRunning}
                onClick={() => onSelectRun(c)}
                className="inline-flex items-center gap-1.5 rounded-md bg-[var(--series-1)] px-3 py-1.5 text-xs font-medium text-white transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
              >
                <Play size={12} />
                Run
              </button>
              <button
                type="button"
                onClick={() => onEdit(c)}
                className="inline-flex items-center gap-1.5 rounded-md border border-[var(--border-hairline)] px-3 py-1.5 text-xs text-[var(--text-secondary)] transition-colors hover:bg-[var(--surface-page)] hover:text-[var(--text-primary)]"
              >
                <Pencil size={12} />
                Edit
              </button>
              <button
                type="button"
                disabled={isRunning}
                onClick={() => onDelete(c)}
                className="ml-auto inline-flex items-center gap-1.5 rounded-md border border-[var(--border-hairline)] px-3 py-1.5 text-xs text-[var(--status-critical)] transition-colors hover:bg-[var(--surface-page)] disabled:cursor-not-allowed disabled:opacity-40"
              >
                <Trash2 size={12} />
                Delete
              </button>
            </div>
          </li>
        )
      })}
    </ul>
  )
}

export default function RunnerTab({ active = true }) {
  const queryClient = useQueryClient()
  const { data, isLoading } = useQuery({
    queryKey: ['runner-configs'],
    queryFn: fetchRunnerConfigs,
  })

  const [formInitial, setFormInitial] = useState(null)
  const [saving, setSaving] = useState(false)
  const [formError, setFormError] = useState(null)

  const [runningId, setRunningId] = useState(null)
  const [runningName, setRunningName] = useState('')
  const [output, setOutput] = useState('')
  const [exitCode, setExitCode] = useState(null)
  const [runError, setRunError] = useState(null)
  const abortRef = useRef(null)
  const consoleRef = useRef(null)

  const configs = data?.configs || []

  const refresh = () =>
    queryClient.invalidateQueries({ queryKey: ['runner-configs'] })

  const handleSubmit = async (form) => {
    setSaving(true)
    setFormError(null)
    try {
      if (formInitial?.id) {
        await updateRunnerConfig(formInitial.id, {
          name: form.name.trim(),
          docker_command: form.docker_command.trim(),
          description: (form.description || '').trim(),
        })
      } else {
        await createRunnerConfig({
          name: form.name.trim(),
          docker_command: form.docker_command.trim(),
          description: (form.description || '').trim(),
        })
      }
      setFormInitial(null)
      await refresh()
    } catch (err) {
      setFormError(err?.message || 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  const stopRun = async () => {
    const runId = abortRef.current?.runId
    abortRef.current?.controller?.abort()
    abortRef.current = null
    if (runId) {
      try {
        await stopRunnerRun(runId)
      } catch {
        /* run may have already exited */
      }
    }
    setRunningId(null)
  }

  const handleRun = async (cfg) => {
    if (runningId) return
    setRunningId(cfg.id)
    setRunningName(cfg.name)
    setOutput('')
    setExitCode(null)
    setRunError(null)

    const controller = new AbortController()
    abortRef.current = { controller, runId: null }

    await streamRunnerRun(cfg.id, {
      signal: controller.signal,
      onStarted: (info) => {
        if (abortRef.current) abortRef.current.runId = info?.run_id
      },
      onStdout: (text) => setOutput((prev) => prev + text),
      onExit: (info) => {
        setExitCode(info?.exit_code ?? null)
        setRunningId(null)
        abortRef.current = null
      },
      onError: (message) => {
        setRunError(message)
        setRunningId(null)
        abortRef.current = null
      },
    })
  }

  const handleDelete = async (cfg) => {
    try {
      await deleteRunnerConfig(cfg.id)
      if (formInitial?.id === cfg.id) setFormInitial(null)
      await refresh()
    } catch (err) {
      setFormError(err?.message || 'Delete failed')
    }
  }

  // Auto-scroll the console as new output arrives (also when the tab is
  // re-shown, since a hidden element has no layout).
  useEffect(() => {
    const el = consoleRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [output, active])

  // Leaving the tab must not stop the run; the component stays mounted.
  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
      <div className="space-y-4 lg:col-span-1">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-[var(--text-primary)]">
            Saved configurations
          </h2>
          <button
            type="button"
            onClick={() => setFormInitial({ ...EMPTY_FORM })}
            className="inline-flex items-center gap-1.5 rounded-md border border-[var(--border-hairline)] px-3 py-1.5 text-xs font-medium text-[var(--text-secondary)] transition-colors hover:bg-[var(--surface-page)] hover:text-[var(--text-primary)]"
          >
            <Plus size={13} />
            New
          </button>
        </div>
        {isLoading ? (
          <div className="flex items-center gap-2 p-4 text-sm text-[var(--text-muted)]">
            <Loader2 size={14} className="animate-spin" /> Loading…
          </div>
        ) : (
          <ConfigList
            configs={configs}
            runningId={runningId}
            onSelectRun={handleRun}
            onEdit={(c) => setFormInitial({ ...c })}
            onDelete={handleDelete}
          />
        )}
      </div>

      <div className="space-y-4 lg:col-span-2">
        <ConfigForm
          initial={formInitial}
          saving={saving}
          error={formError}
          onSubmit={handleSubmit}
          onCancel={() => setFormInitial(null)}
        />

        <div className="rounded-xl border border-[var(--border-hairline)] bg-[var(--surface-card)]">
          <div className="flex items-center justify-between border-b border-[var(--border-hairline)] px-4 py-2.5">
            <div className="flex items-center gap-2">
              <Terminal size={15} className="text-[var(--series-1)]" />
              <h3 className="text-sm font-semibold text-[var(--text-primary)]">
                Run console
              </h3>
              {runningName && (
                <span className="text-xs text-[var(--text-muted)]">
                  · {runningName}
                </span>
              )}
            </div>
            <div className="flex items-center gap-3">
              {runningId ? (
                <span className="inline-flex items-center gap-1.5 text-xs font-medium text-[var(--status-good)]">
                  <Loader2 size={12} className="animate-spin" /> Running
                </span>
              ) : exitCode !== null ? (
                <span
                  className={`text-xs font-medium ${
                    exitCode === 0
                      ? 'text-[var(--status-good)]'
                      : 'text-[var(--status-critical)]'
                  }`}
                >
                  Exited ({exitCode})
                </span>
              ) : (
                <span className="text-xs text-[var(--text-muted)]">Idle</span>
              )}
              {runningId && (
                <button
                  type="button"
                  onClick={stopRun}
                  className="inline-flex items-center gap-1.5 rounded-md border border-[var(--status-critical)] px-3 py-1.5 text-xs font-medium text-[var(--status-critical)] transition-colors hover:bg-[var(--surface-page)]"
                >
                  <Square size={12} />
                  Stop
                </button>
              )}
            </div>
          </div>
          <div
            ref={consoleRef}
            className="h-[420px] overflow-auto bg-[var(--surface-page)] p-3"
          >
            {runError && (
              <div className="mb-2 rounded-lg border border-[var(--status-critical)] px-3 py-1.5 text-xs text-[var(--status-critical)]">
                {runError}
              </div>
            )}
            {output ? (
              <pre className="whitespace-pre-wrap break-words font-mono text-xs leading-relaxed text-[var(--text-secondary)]">
                {output}
                {runningId && <span className="animate-pulse">▍</span>}
              </pre>
            ) : (
              <p className="text-xs text-[var(--text-muted)]">
                Select a configuration and press Run to start the container.
                Output streams here live.
              </p>
            )}
          </div>
          {exitCode !== null && !runningId && (
            <div className="border-t border-[var(--border-hairline)] px-4 py-2 text-[11px] text-[var(--text-muted)]">
              Last run finished at {fmtTime(new Date().toISOString())} · exit code{' '}
              {exitCode}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
