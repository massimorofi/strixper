import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { KeyRound, Loader2, Plus, RefreshCw, Server, ShieldAlert } from 'lucide-react'
import {
  deleteMCPServer,
  fetchAgentSkills,
  fetchMCPServers,
  saveMCPServer,
  testMCPServer,
} from '../api.js'

const blankDraft = () => ({
  id: '',
  command: '',
  args: '[]',
  env: '{}',
  enabled: false,
  minimum_access: 'read-only',
})

function inputClass() {
  return 'w-full rounded-md border border-[var(--border-hairline)] bg-[var(--surface-page)] px-2.5 py-1.5 text-xs text-[var(--text-primary)] focus:outline-none focus:ring-2 focus:ring-[var(--series-1)]'
}

function mcpPayload(draft) {
  const args = JSON.parse(draft.args)
  const envRefs = JSON.parse(draft.env)
  if (!Array.isArray(args) || args.some((arg) => typeof arg !== 'string')) {
    throw new Error('Arguments must be a JSON array of strings.')
  }
  if (!envRefs || Array.isArray(envRefs) || typeof envRefs !== 'object') {
    throw new Error('Environment references must be a JSON object.')
  }
  const env = Object.fromEntries(
    Object.entries(envRefs).map(([name, secretRef]) => {
      if (typeof secretRef !== 'string') {
        throw new Error('Each environment value must name a backend environment variable.')
      }
      return [name, { secretRef }]
    }),
  )
  return {
    id: draft.id.trim(),
    transport: 'stdio',
    command: draft.command.trim(),
    args,
    env,
    enabled: draft.enabled,
    minimum_access: draft.minimum_access,
  }
}

export default function AgentExtensions({ active = true }) {
  const [adminToken, setAdminToken] = useState('')
  const [draft, setDraft] = useState(blankDraft)
  const [editing, setEditing] = useState(false)
  const [message, setMessage] = useState('')
  const queryClient = useQueryClient()
  const skillsQuery = useQuery({
    queryKey: ['agent-skills'],
    queryFn: fetchAgentSkills,
    enabled: active,
  })
  const mcpQuery = useQuery({
    queryKey: ['mcp-servers', adminToken],
    queryFn: () => fetchMCPServers(adminToken),
    enabled: active && Boolean(adminToken),
    retry: false,
  })
  const saveMutation = useMutation({
    mutationFn: (payload) => saveMCPServer(adminToken, payload, editing),
    onSuccess: async () => {
      setMessage('MCP server configuration saved.')
      setDraft(blankDraft())
      setEditing(false)
      await queryClient.invalidateQueries({ queryKey: ['mcp-servers'] })
    },
    onError: (error) => setMessage(error.message),
  })
  const deleteMutation = useMutation({
    mutationFn: (id) => deleteMCPServer(adminToken, id),
    onSuccess: async () => {
      setMessage('MCP server removed.')
      await queryClient.invalidateQueries({ queryKey: ['mcp-servers'] })
    },
    onError: (error) => setMessage(error.message),
  })
  const testMutation = useMutation({
    mutationFn: (payload) => testMCPServer(adminToken, payload),
    onSuccess: (result) => {
      setMessage(
        result.ok
          ? `Connection succeeded; ${result.tool_count} tool(s) discovered.`
          : result.error,
      )
      queryClient.invalidateQueries({ queryKey: ['mcp-servers'] })
    },
    onError: (error) => setMessage(error.message),
  })

  const refresh = () => {
    skillsQuery.refetch()
    if (adminToken) mcpQuery.refetch()
  }

  const editServer = (server) => {
    setDraft({
      id: server.id,
      command: server.command,
      args: JSON.stringify(server.args, null, 2),
      env: JSON.stringify(
        Object.fromEntries(
          Object.entries(server.env || {}).map(([key, value]) => [key, value.secretRef]),
        ),
        null,
        2,
      ),
      enabled: server.enabled,
      minimum_access: server.minimum_access,
    })
    setEditing(true)
    setMessage('')
  }

  const handleSave = () => {
    setMessage('')
    try {
      const payload = mcpPayload(draft)
      if (payload.enabled && !window.confirm(
        'Enabling this stdio MCP server runs its command as the Strixper backend user. It inherits container mounts and may have access to the Docker socket. Enable only a command you trust.',
      )) return
      saveMutation.mutate(payload)
    } catch (error) {
      setMessage(error.message || 'Invalid server configuration.')
    }
  }

  const handleTest = () => {
    setMessage('')
    try {
      if (!window.confirm(
        'Testing this stdio MCP server launches its command as the Strixper backend user. It inherits container mounts and may have access to the Docker socket. Continue only if you trust it.',
      )) return
      testMutation.mutate(mcpPayload({ ...draft, enabled: true }))
    } catch (error) {
      setMessage(error.message || 'Invalid server configuration.')
    }
  }

  const skills = skillsQuery.data?.skills || []
  const servers = mcpQuery.data?.servers || []

  return (
    <div className="min-h-0 flex-1 space-y-4 overflow-y-auto rounded-xl border border-[var(--border-hairline)] bg-[var(--surface-card)] p-4">
      <div className="flex flex-wrap items-center gap-2">
        <div className="mr-auto">
          <h2 className="text-sm font-semibold text-[var(--text-primary)]">Agent Extensions</h2>
          <p className="text-xs text-[var(--text-muted)]">
            Drop skill folders into the configured skills directory. MCP servers are explicit, trusted tools.
          </p>
        </div>
        <button
          type="button"
          onClick={refresh}
          className="inline-flex items-center gap-1.5 rounded-md border border-[var(--border-hairline)] px-2.5 py-1.5 text-xs text-[var(--text-secondary)] hover:bg-[var(--surface-page)]"
        >
          <RefreshCw size={13} /> Rescan / refresh
        </button>
      </div>

      <section className="rounded-lg border border-[var(--border-hairline)] p-3">
        <h3 className="mb-1 text-xs font-semibold text-[var(--text-primary)]">Skills</h3>
        <p className="mb-3 break-all font-mono text-[11px] text-[var(--text-muted)]">
          {skillsQuery.data?.directory || 'Loading skills directory…'}
        </p>
        {skillsQuery.isError && (
          <p className="mb-2 text-xs text-[var(--status-critical)]">{skillsQuery.error.message}</p>
        )}
        {skills.length === 0 ? (
          <p className="text-xs text-[var(--text-muted)]">No valid skills found. Each folder must contain a valid SKILL.md.</p>
        ) : (
          <ul className="space-y-2">
            {skills.map((skill) => (
              <li key={skill.directory} className="rounded-md bg-[var(--surface-page)] px-3 py-2">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-xs font-medium text-[var(--text-primary)]">
                    {skill.valid ? skill.name : skill.directory}
                  </span>
                  <span className={`text-[10px] ${skill.valid ? 'text-[var(--status-good)]' : 'text-[var(--status-critical)]'}`}>
                    {skill.valid ? 'Ready' : 'Invalid'}
                  </span>
                </div>
                <p className="mt-0.5 text-xs text-[var(--text-muted)]">
                  {skill.valid ? skill.description : skill.error}
                </p>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="space-y-3 rounded-lg border border-[var(--border-hairline)] p-3">
        <div className="flex items-center gap-2">
          <Server size={14} className="text-[var(--series-1)]" />
          <h3 className="mr-auto text-xs font-semibold text-[var(--text-primary)]">MCP Servers</h3>
        </div>
        <div className="rounded-md border border-[var(--status-warning)]/40 bg-[var(--surface-page)] p-2.5 text-xs text-[var(--text-secondary)]">
          <p className="flex items-center gap-1.5 font-medium text-[var(--status-warning)]">
            <ShieldAlert size={13} /> Administrative access required
          </p>
          <p className="mt-1">
            Set <code>MCP_ADMIN_TOKEN</code> in the backend environment. An enabled stdio server runs with the backend container&apos;s permissions and mounts.
          </p>
        </div>
        <label className="block text-xs text-[var(--text-secondary)]">
          <span className="mb-1 flex items-center gap-1"><KeyRound size={12} /> MCP administrator token</span>
          <input
            type="password"
            autoComplete="off"
            value={adminToken}
            onChange={(event) => setAdminToken(event.target.value)}
            placeholder="Not stored by this page"
            className={inputClass()}
          />
        </label>
        {mcpQuery.isError && (
          <p className="text-xs text-[var(--status-critical)]">{mcpQuery.error.message}</p>
        )}
        {mcpQuery.data?.config_error && (
          <p className="text-xs text-[var(--status-critical)]">{mcpQuery.data.config_error}</p>
        )}

        {mcpQuery.data?.configured && (
          <>
            {servers.length === 0 ? (
              <p className="text-xs text-[var(--text-muted)]">No MCP servers configured.</p>
            ) : (
              <ul className="space-y-2">
                {servers.map((server) => (
                  <li key={server.id} className="flex flex-wrap items-center gap-2 rounded-md bg-[var(--surface-page)] p-2.5">
                    <div className="mr-auto min-w-40">
                      <p className="text-xs font-medium text-[var(--text-primary)]">
                        {server.id} · {server.status}
                      </p>
                      <p className="text-[11px] text-[var(--text-muted)]">
                        {server.minimum_access} · {server.tools?.length || 0} tools
                      </p>
                      {server.error && <p className="text-[11px] text-[var(--status-critical)]">{server.error}</p>}
                    </div>
                    <button type="button" onClick={() => editServer(server)} className="text-xs text-[var(--series-1)]">Edit</button>
                    <button
                      type="button"
                      onClick={() => {
                        if (window.confirm(`Delete MCP server "${server.id}"?`)) deleteMutation.mutate(server.id)
                      }}
                      className="text-xs text-[var(--status-critical)]"
                    >
                      Delete
                    </button>
                  </li>
                ))}
              </ul>
            )}

            <div className="grid gap-2 sm:grid-cols-2">
              <label className="text-xs text-[var(--text-secondary)]">
                Server ID
                <input value={draft.id} disabled={editing} onChange={(e) => setDraft((v) => ({ ...v, id: e.target.value }))} className={inputClass()} />
              </label>
              <label className="text-xs text-[var(--text-secondary)]">
                Command
                <input value={draft.command} onChange={(e) => setDraft((v) => ({ ...v, command: e.target.value }))} className={inputClass()} placeholder="e.g. docker" />
              </label>
              <label className="text-xs text-[var(--text-secondary)]">
                Arguments (JSON array)
                <textarea value={draft.args} onChange={(e) => setDraft((v) => ({ ...v, args: e.target.value }))} rows={3} className={`${inputClass()} font-mono`} />
              </label>
              <label className="text-xs text-[var(--text-secondary)]">
                Environment name → backend secret reference name (JSON; never a secret value)
                <textarea value={draft.env} onChange={(e) => setDraft((v) => ({ ...v, env: e.target.value }))} rows={3} className={`${inputClass()} font-mono`} placeholder={'{"API_TOKEN":"MCP_API_TOKEN"}'} />
              </label>
              <label className="text-xs text-[var(--text-secondary)]">
                Minimum access
                <select value={draft.minimum_access} onChange={(e) => setDraft((v) => ({ ...v, minimum_access: e.target.value }))} className={inputClass()}>
                  <option value="read-only">Read-only</option>
                  <option value="actions">Actions</option>
                  <option value="full_access">Full access</option>
                </select>
              </label>
              <label className="flex items-center gap-2 self-end pb-2 text-xs text-[var(--text-secondary)]">
                <input type="checkbox" checked={draft.enabled} onChange={(e) => setDraft((v) => ({ ...v, enabled: e.target.checked }))} className="accent-[var(--series-1)]" />
                Enabled
              </label>
            </div>
            <div className="flex flex-wrap gap-2">
              <button type="button" onClick={handleSave} disabled={saveMutation.isPending} className="inline-flex items-center gap-1.5 rounded-md bg-[var(--series-1)] px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50">
                {saveMutation.isPending ? <Loader2 size={13} className="animate-spin" /> : <Plus size={13} />}
                {editing ? 'Save changes' : 'Add server'}
              </button>
              <button type="button" onClick={handleTest} disabled={testMutation.isPending} className="rounded-md border border-[var(--border-hairline)] px-3 py-1.5 text-xs text-[var(--text-secondary)] disabled:opacity-50">
                {testMutation.isPending ? 'Testing…' : 'Test connection'}
              </button>
              {editing && (
                <button type="button" onClick={() => { setDraft(blankDraft()); setEditing(false) }} className="px-2 py-1.5 text-xs text-[var(--text-muted)]">
                  Cancel edit
                </button>
              )}
            </div>
            {message && <p className="whitespace-pre-wrap text-xs text-[var(--text-secondary)]">{message}</p>}
          </>
        )}
      </section>
    </div>
  )
}
