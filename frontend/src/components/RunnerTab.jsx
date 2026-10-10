import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ChevronDown,
  ChevronRight,
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
  fetchRunnerRuns,
  openRunnerStream,
  startRunnerRun,
  stopRunnerRun,
  updateRunnerConfig,
} from '../api.js'
import {
  MAX_PARAMS,
  missingParameters,
  normaliseRows,
  renderTemplate,
  unusedParameters,
  validateRows,
} from '../params.js'

// The console is a live firehose; re-rendering a React string on every
// chunk is O(length) each time and visibly janks a long run. Chunks are
// collected in a ref and flushed on a fixed beat instead, and the rendered
// string is capped so memory and re-render cost stay flat.
const CONSOLE_FLUSH_MS = 100
const CONSOLE_MAX_CHARS = 500_000

const EMPTY_FORM = {
  name: '',
  docker_command: '',
  description: '',
  parameters: [],
}

const inputCls =
  'w-full rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-page)] px-3 py-2 text-sm text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:border-[var(--series-1)] focus:outline-none'
const monoCls =
  'w-full rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-page)] px-3 py-2 font-mono text-xs text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:border-[var(--series-1)] focus:outline-none'

/**
 * Editable list of run parameters. Each parameter is a named value the
 * command template references as `{name}`; rows can be added and removed
 * freely. Name validity and duplicate detection are reported per row.
 */
function ParametersEditor({ params, onChange }) {
  const rowErrors = validateRows(params)

  const update = (i, key, value) =>
    onChange(params.map((p, j) => (j === i ? { ...p, [key]: value } : p)))
  const add = () =>
    onChange([...params, { name: '', label: '', value: '' }])
  const remove = (i) => onChange(params.filter((_, j) => j !== i))

  return (
    <div>
      <div className="mb-1 flex items-center justify-between">
        <span className="text-xs font-medium uppercase tracking-wide text-[var(--text-muted)]">
          Run parameters
        </span>
        <button
          type="button"
          onClick={add}
          disabled={params.length >= MAX_PARAMS}
          className="inline-flex items-center gap-1 rounded-md border border-[var(--border-hairline)] px-2 py-1 text-[11px] font-medium text-[var(--text-secondary)] transition-colors hover:bg-[var(--surface-card)] hover:text-[var(--text-primary)] disabled:cursor-not-allowed disabled:opacity-40"
        >
          <Plus size={12} />
          Add parameter
        </button>
      </div>

      {params.length === 0 ? (
        <p className="rounded-lg border border-dashed border-[var(--border-hairline)] px-3 py-2 text-xs text-[var(--text-muted)]">
          No parameters yet. Write a variable part of the command as{' '}
          <code className="font-mono text-[var(--text-secondary)]">{'{name}'}</code>{' '}
          and add a matching parameter here.
        </p>
      ) : (
        <div className="space-y-2">
          {params.map((p, i) => (
            <div
              key={i}
              className="grid grid-cols-1 items-start gap-2 rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-card)] p-2 sm:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)_minmax(0,1.6fr)_auto]"
            >
              <input
                type="text"
                value={p.name}
                onChange={(e) => update(i, 'name', e.target.value)}
                maxLength={60}
                placeholder="name"
                spellCheck={false}
                className={`font-mono text-xs ${inputCls} ${
                  rowErrors[i]
                    ? 'border-[var(--status-critical)]'
                    : 'border-transparent'
                }`}
              />
              <input
                type="text"
                value={p.label}
                onChange={(e) => update(i, 'label', e.target.value)}
                maxLength={120}
                placeholder="Label (optional)"
                className={inputCls}
              />
              <input
                type="text"
                value={p.value}
                onChange={(e) => update(i, 'value', e.target.value)}
                placeholder="value"
                spellCheck={false}
                className={monoCls}
              />
              <button
                type="button"
                onClick={() => remove(i)}
                title={`Remove ${p.name || 'parameter'}`}
                className="mt-1 inline-flex items-center rounded-md border border-[var(--border-hairline)] p-2 text-[var(--status-critical)] transition-colors hover:bg-[var(--surface-page)]"
              >
                <Trash2 size={13} />
              </button>
              {rowErrors[i] && (
                <p className="text-[11px] text-[var(--status-critical)] sm:col-span-4">
                  {rowErrors[i]}
                </p>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

/** Collapsible view of the command the run will actually launch. */
function CommandPreview({ command }) {
  const [open, setOpen] = useState(false)
  if (!command) return null
  return (
    <div className="rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-page)]">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-1.5 px-3 py-2 text-left text-xs font-medium text-[var(--text-secondary)] transition-colors hover:text-[var(--text-primary)]"
      >
        {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
        Rendered command
      </button>
      {open && (
        <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words border-t border-[var(--border-hairline)] p-3 font-mono text-[11px] leading-relaxed text-[var(--text-secondary)]">
          {command}
        </pre>
      )}
    </div>
  )
}

function ConfigForm({ initial, saving, error, onSubmit, onCancel }) {
  const [form, setForm] = useState(initial || EMPTY_FORM)
  const isEdit = Boolean(initial?.id)

  useEffect(() => {
    setForm(
      initial
        ? { ...initial, parameters: normaliseRows(initial.parameters) }
        : EMPTY_FORM,
    )
  }, [initial])

  const set = (key) => (e) => setForm((f) => ({ ...f, [key]: e.target.value }))

  // Live feedback on how the command template and the parameter list line
  // up. The backend re-checks both on save; this just avoids the surprise.
  const missing = useMemo(
    () => missingParameters(form.docker_command || '', form.parameters || []),
    [form.docker_command, form.parameters],
  )
  const unused = useMemo(
    () => unusedParameters(form.docker_command || '', form.parameters || []),
    [form.docker_command, form.parameters],
  )
  const rowErrors = useMemo(
    () => validateRows(form.parameters || []).some(Boolean),
    [form.parameters],
  )
  const preview = useMemo(() => {
    try {
      return renderTemplate(form.docker_command || '', form.parameters || [])
    } catch {
      return null
    }
  }, [form.docker_command, form.parameters])

  const canSubmit =
    form.name.trim() && form.docker_command.trim() && !saving && !rowErrors

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
            className={inputCls}
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
            placeholder={
              'docker run --rm -it -v {models}/weights.hgn:/models/w.hgn:ro -p 0.0.0.0:{port}:{port} my-llm-image'
            }
            className={`${monoCls} resize-y`}
          />
        </label>

        <ParametersEditor
          params={form.parameters || []}
          onChange={(parameters) => setForm((f) => ({ ...f, parameters }))}
        />

        {missing.length > 0 && (
          <div className="rounded-lg border border-[var(--status-warning)] px-3 py-1.5 text-xs text-[var(--status-warning)]">
            Command uses {missing.map((n) => `{${n}}`).join(', ')} but no such
            parameter is defined.
          </div>
        )}
        {unused.length > 0 && (
          <div className="rounded-lg border border-[var(--border-hairline)] px-3 py-1.5 text-xs text-[var(--text-muted)]">
            Not referenced by the command:{' '}
            {unused.map((n) => `{${n}}`).join(', ')}
          </div>
        )}

        <CommandPreview command={preview} />

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
            className={inputCls}
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
            onClick={() =>
              onSubmit({
                name: form.name,
                docker_command: form.docker_command,
                description: form.description,
                parameters: form.parameters || [],
              })
            }
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

function ConfigList({
  configs,
  activeConfigIds,
  stopping,
  onSelectRun,
  onStop,
  onEdit,
  onDelete,
}) {
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
        const isRunning = activeConfigIds.has(c.id)
        const params = c.parameters || []
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
                {params.length > 0 && (
                  <div className="mt-1.5 flex flex-wrap gap-1">
                    {params.map((p) => (
                      <span
                        key={p.name}
                        className="rounded bg-[var(--surface-page)] px-1.5 py-0.5 font-mono text-[10px] text-[var(--text-secondary)]"
                        title={`${p.label || p.name}: ${p.value}`}
                      >
                        {p.name}={p.value}
                      </span>
                    ))}
                  </div>
                )}
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
              {/* Stop lives on every card rather than only the console, so a
                  browser that merely opened the page can still see and
                  control the running engine. Disabled rather than hidden
                  when idle, so the layout never jumps. */}
              <button
                type="button"
                disabled={!isRunning || stopping}
                onClick={() => onStop(c)}
                title={
                  isRunning
                    ? 'Stop this engine'
                    : 'Nothing is running from this configuration'
                }
                className="inline-flex items-center gap-1.5 rounded-md border border-[var(--status-critical)] px-3 py-1.5 text-xs font-medium text-[var(--status-critical)] transition-colors hover:bg-[var(--surface-page)] disabled:cursor-not-allowed disabled:border-[var(--border-hairline)] disabled:text-[var(--text-muted)] disabled:opacity-60"
              >
                <Square size={12} />
                {stopping && isRunning ? 'Stopping…' : 'Stop'}
              </button>
              <button
                type="button"
                onClick={() => onEdit(c)}
                className="ml-auto inline-flex items-center gap-1.5 rounded-md border border-[var(--border-hairline)] px-3 py-1.5 text-xs text-[var(--text-secondary)] transition-colors hover:bg-[var(--surface-page)] hover:text-[var(--text-primary)]"
              >
                <Pencil size={12} />
                Edit
              </button>
              <button
                type="button"
                disabled={isRunning}
                onClick={() => onDelete(c)}
                className="inline-flex items-center gap-1.5 rounded-md border border-[var(--border-hairline)] px-3 py-1.5 text-xs text-[var(--status-critical)] transition-colors hover:bg-[var(--surface-page)] disabled:cursor-not-allowed disabled:opacity-40"
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

export default function RunnerTab({ active = true, onEngineChange }) {
  const queryClient = useQueryClient()
  const { data, isLoading } = useQuery({
    queryKey: ['runner-configs'],
    queryFn: fetchRunnerConfigs,
  })

  // The run is a server-side resource, so its state is read from the
  // backend rather than kept in this component. Polling means a run that
  // exits on its own -- a crash, an OOM -- is reflected in the buttons
  // even with no console stream open, and a run started in another browser
  // shows up here too.
  const { data: runs = [] } = useQuery({
    queryKey: ['runner-runs'],
    queryFn: fetchRunnerRuns,
    refetchInterval: 3000,
  })

  const [formInitial, setFormInitial] = useState(null)
  const [saving, setSaving] = useState(false)
  const [formError, setFormError] = useState(null)

  const [stopping, setStopping] = useState(false)
  const [selectedRunId, setSelectedRunId] = useState(null)
  const [outputs, setOutputs] = useState({})
  const [runErrors, setRunErrors] = useState({})
  const pendingRef = useRef({})
  const consoleRef = useRef(null)

  const configs = data?.configs || []
  const runningRuns = runs.filter((run) => run.status === 'running')
  const runningRunIds = runningRuns.map((run) => run.run_id).join(',')
  const selectedRun = runs.find((run) => run.run_id === selectedRunId) || runningRuns[0] || runs[0]
  const output = selectedRun ? outputs[selectedRun.run_id] || '' : ''
  const activeConfigIds = new Set(runningRuns.map((run) => run.config_id))

  const refresh = () =>
    queryClient.invalidateQueries({ queryKey: ['runner-configs'] })

  const refreshRunState = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ['runner-runs'] })
    queryClient.invalidateQueries({ queryKey: ['engine-target'] })
    queryClient.invalidateQueries({ queryKey: ['live-status'] })
  }, [queryClient])

  const handleSubmit = async (form) => {
    setSaving(true)
    setFormError(null)
    const payload = {
      name: form.name.trim(),
      docker_command: form.docker_command.trim(),
      description: (form.description || '').trim(),
      parameters: (form.parameters || []).map((p) => ({
        name: (p.name || '').trim(),
        label: (p.label || '').trim(),
        value: p.value ?? '',
      })),
    }
    try {
      if (formInitial?.id) {
        await updateRunnerConfig(formInitial.id, payload)
      } else {
        await createRunnerConfig(payload)
      }
      setFormInitial(null)
      await refresh()
    } catch (err) {
      setFormError(err?.message || 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  const handleRun = async (cfg) => {
    setRunErrors((prev) => ({ ...prev, start: null }))
    try {
      const started = await startRunnerRun(cfg.id)
      setSelectedRunId(started.run_id)
      refreshRunState()
      onEngineChange?.()
    } catch (err) {
      setRunErrors((prev) => ({ ...prev, start: err?.message || 'Failed to start' }))
    }
  }

  // Stop goes through the backend, which stops the *container* via docker
  // rather than just killing the client -- killing the `docker run` client
  // leaves the container up. `docker stop` can take a few seconds, so the
  // button shows progress rather than appearing to hang.
  const handleStop = async (config) => {
    const run = runningRuns.find((item) => item.config_id === config.id)
    const runId = run?.run_id
    if (!runId) return
    setSelectedRunId(runId)
    setStopping(true)
    try {
      await stopRunnerRun(runId)
    } catch (err) {
      setRunErrors((prev) => ({
        ...prev,
        [runId]: err?.message || 'Failed to stop engine',
      }))
    } finally {
      setStopping(false)
      refreshRunState()
    }
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

  // Keep an independent console stream for every running engine.
  useEffect(() => {
    const controllers = runningRuns.map((run) => {
      const controller = new AbortController()
      openRunnerStream(run.run_id, {
        signal: controller.signal,
        onState: (info) => {
          pendingRef.current[run.run_id] = ''
          setOutputs((prev) => ({ ...prev, [run.run_id]: info?.backlog || '' }))
        },
        onStdout: (text) => {
          pendingRef.current[run.run_id] = (pendingRef.current[run.run_id] || '') + text
        },
        onExit: () => refreshRunState(),
        onError: (message) => setRunErrors((prev) => ({ ...prev, [run.run_id]: message })),
      })
      return controller
    })
    return () => controllers.forEach((controller) => controller.abort())
  }, [runningRunIds, refreshRunState])

  // Flush buffered console output on a fixed beat rather than per chunk.
  useEffect(() => {
    const id = setInterval(() => {
      const chunks = pendingRef.current
      pendingRef.current = {}
      if (!Object.keys(chunks).length) return
      setOutputs((prev) => {
        const next = { ...prev }
        for (const [runId, chunk] of Object.entries(chunks)) {
          const joined = (next[runId] || '') + chunk
          next[runId] = joined.length > CONSOLE_MAX_CHARS
            ? joined.slice(joined.length - CONSOLE_MAX_CHARS)
            : joined
        }
        return next
      })
    }, CONSOLE_FLUSH_MS)
    return () => clearInterval(id)
  }, [])

  // Auto-scroll the console as new output arrives (also when the tab is
  // re-shown, since a hidden element has no layout).
  useEffect(() => {
    const el = consoleRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [output, active, selectedRun?.run_id])

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
            activeConfigIds={activeConfigIds}
            stopping={stopping}
            onSelectRun={handleRun}
            onStop={handleStop}
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
            <div className="flex min-w-0 items-center gap-2">
              <Terminal size={15} className="shrink-0 text-[var(--series-1)]" />
              <h3 className="shrink-0 text-sm font-semibold text-[var(--text-primary)]">
                Run console
              </h3>
              {selectedRun?.config_name && (
                <span className="truncate text-xs text-[var(--text-muted)]">
                  · {selectedRun.config_name}
                </span>
              )}
              {selectedRun?.container_name && (
                <span
                  className="hidden font-mono text-[11px] text-[var(--text-muted)] md:inline"
                  title="Container stopped by docker when you press Stop"
                >
                  · {selectedRun.container_name}
                </span>
              )}
              {selectedRun?.engine_url && (
                <span
                  className="hidden font-mono text-[11px] text-[var(--series-1)] lg:inline"
                  title="The dashboard now reads its live metrics from this engine"
                >
                  · dashboard → {selectedRun.engine_url}
                </span>
              )}
              {selectedRun?.adopted && (
                <span
                  className="hidden text-[11px] text-[var(--text-muted)] xl:inline"
                  title="This engine was already running when the dashboard opened; it was picked up rather than launched here."
                >
                  · adopted
                </span>
              )}
            </div>
            <div className="flex shrink-0 items-center gap-3">
              {selectedRun?.status === 'running' ? (
                <span
                  className={`inline-flex items-center gap-1.5 text-xs font-medium ${
                    stopping
                      ? 'text-[var(--status-warning)]'
                      : 'text-[var(--status-good)]'
                  }`}
                >
                  <Loader2 size={12} className="animate-spin" />
                  {stopping ? 'Stopping container…' : 'Running'}
                </span>
              ) : selectedRun?.status === 'exited' ? (
                <span
                  className={`text-xs font-medium ${
                    selectedRun.exit_code === 0
                      ? 'text-[var(--status-good)]'
                      : 'text-[var(--status-critical)]'
                  }`}
                >
                  Exited ({selectedRun.exit_code})
                </span>
              ) : (
                <span className="text-xs text-[var(--text-muted)]">Idle</span>
              )}
            </div>
          </div>
          {runningRuns.length > 0 && (
            <div className="flex gap-1 overflow-x-auto border-b border-[var(--border-hairline)] px-3 py-2">
              {runningRuns.map((run) => (
                <button
                  key={run.run_id}
                  type="button"
                  onClick={() => setSelectedRunId(run.run_id)}
                  className={`max-w-52 truncate rounded-md px-3 py-1.5 text-xs ${
                    selectedRun?.run_id === run.run_id
                      ? 'bg-[var(--series-1)] text-white'
                      : 'text-[var(--text-secondary)] hover:bg-[var(--surface-page)]'
                  }`}
                  title={run.container_name || run.config_name}
                >
                  {run.config_name}
                </button>
              ))}
            </div>
          )}
          <div
            ref={consoleRef}
            className="h-[420px] overflow-auto bg-[var(--surface-page)] p-3"
          >
            {(runErrors[selectedRun?.run_id] || runErrors.start) && (
              <div className="mb-2 rounded-lg border border-[var(--status-critical)] px-3 py-1.5 text-xs text-[var(--status-critical)]">
                {runErrors[selectedRun?.run_id] || runErrors.start}
              </div>
            )}
            {output ? (
              <pre className="whitespace-pre-wrap break-words font-mono text-xs leading-relaxed text-[var(--text-secondary)]">
                {output}
                {selectedRun?.status === 'running' && <span className="animate-pulse">▍</span>}
              </pre>
            ) : (
              <p className="text-xs text-[var(--text-muted)]">
                {selectedRun?.status === 'running'
                  ? 'Waiting for output…'
                  : 'Select a configuration and press Run to start the container. Output streams here live.'}
              </p>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
