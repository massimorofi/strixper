import { useState } from 'react'
import { Info } from 'lucide-react'
import { CHART } from '../theme.js'
import { fmtInt, fmtNum, fmtPct } from '../format.js'
import StatTile from './StatTile.jsx'
import Meter from './Meter.jsx'
import StatsSummary from './StatsSummary.jsx'
import InfoModal from '../charts/InfoModal.jsx'
import ThroughputChart from '../charts/ThroughputChart.jsx'
import KvPoolChart from '../charts/KvPoolChart.jsx'
import QueueChart from '../charts/QueueChart.jsx'
import MemoryChart from '../charts/MemoryChart.jsx'

export default function LiveTab({ data, history, theme, onResetStats }) {
  const [kvInfo, setKvInfo] = useState(false)
  const colors = CHART[theme] || CHART.dark
  const halogen = data?.halogen || {}
  const hardware = data?.hardware || {}
  const gpu = hardware.gpu || {}
  const mem = hardware.memory || {}
  const cpu = hardware.cpu || {}
  const stats = data?.stats || {}

  const kvPct = (halogen.kv_cache_usage_ratio ?? 0) * 100

  return (
    <div className="space-y-6">
      {/* Primary KPI cards */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatTile
          label="Model Loaded"
          value={halogen.model || '—'}
          sub={
            halogen.context_size
              ? `Context window: ${fmtInt(halogen.context_size)} tokens`
              : 'No model reported'
          }
          info={
            <>
              <p>
                The model currently loaded into the Halogen engine, and the maximum
                context window it was configured with.
              </p>
              <p>
                The <strong className="text-[var(--text-primary)]">context window</strong>{' '}
                is the total number of tokens (input + generated output) the model
                can hold in active memory at once. A larger context lets you feed
                longer documents or hold longer conversations, but it consumes
                more of the KV cache.
              </p>
            </>
          }
        />
        <StatTile
          label="In-Flight & Slots"
          value={`${fmtInt(halogen.in_flight)} / ${fmtInt(halogen.slots)} busy`}
          sub={`${fmtInt(halogen.queued)} queued`}
          info={
            <>
              <p>
                How much of the engine's request concurrency is in use right now.
              </p>
              <ul className="list-disc space-y-1.5 pl-5">
                <li>
                  <strong className="text-[var(--text-primary)]">In-flight</strong>{' '}
                  — requests currently being processed (actively generating).
                </li>
                <li>
                  <strong className="text-[var(--text-primary)]">Slots</strong> —
                  the engine's total concurrency: the maximum number of requests it
                  can process simultaneously (here, 4).
                </li>
                <li>
                  <strong className="text-[var(--text-primary)]">Queued</strong> —
                  requests waiting because all slots are busy. 0 means no backlog;
                  a rising queue means requests are waiting and latency is climbing.
                </li>
              </ul>
            </>
          }
        />
        <div className="rounded-xl border border-[var(--border-hairline)] bg-[var(--surface-card)] p-4">
          <div className="flex items-start justify-between gap-2">
            <div className="text-xs font-medium uppercase tracking-wide text-[var(--text-muted)]">
              KV Cache Pool
            </div>
            <button
              type="button"
              onClick={() => setKvInfo(true)}
              aria-label="About KV Cache Pool"
              title="What this measure means"
              className="-mr-1 -mt-1 shrink-0 rounded-md p-1 text-[var(--text-muted)] transition-colors hover:bg-[var(--surface-page)] hover:text-[var(--text-primary)]"
            >
              <Info className="h-3.5 w-3.5" />
            </button>
          </div>
          <div className="mt-1 text-2xl font-semibold text-[var(--text-primary)]">
            {fmtPct(kvPct)}
          </div>
          <div className="mt-2">
            <Meter
              pct={kvPct}
              color={kvPct > 90 ? 'var(--status-critical)' : 'var(--series-1)'}
            />
          </div>
          <div className="mt-1.5 text-sm text-[var(--text-secondary)]">
            {fmtInt(halogen.kv_pool_positions)} positions
          </div>
          {kvInfo && (
            <InfoModal title="KV Cache Pool" onClose={() => setKvInfo(false)}>
              <p>
                The fraction of the model's KV (key-value) cache currently
                occupied.
              </p>
              <p>
                The KV cache stores the attention state computed for every token in
                the active context, so it grows as conversations get longer and is
                the main GPU-memory consumer during generation.
              </p>
              <p>
                When it approaches 100%, new requests may be deferred, older
                context may be evicted, and you may hit context-length limits. The
                number below the bar is the total cache capacity in positions.
              </p>
            </InfoModal>
          )}
        </div>
        <StatTile
          label="Throughput (session avg)"
          value={`${fmtNum(stats.prompt_tps?.avg, 0)} t/s prefill`}
          sub={`${fmtNum(stats.decode_tps?.avg, 1)} t/s decode · draft acceptance ${
            halogen.draft_acceptance_rate != null
              ? fmtPct(halogen.draft_acceptance_rate * 100)
              : '—'
          }`}
          info={
            <>
              <p>
                Average token throughput since the dashboard started (or since the
                last statistics reset).
              </p>
              <ul className="list-disc space-y-1.5 pl-5">
                <li>
                  <strong className="text-[var(--text-primary)]">
                    Prefill (t/s)
                  </strong>{' '}
                  — average speed at which the model reads and encodes your input.
                </li>
                <li>
                  <strong className="text-[var(--text-primary)]">
                    Decode (t/s)
                  </strong>{' '}
                  — average speed at which the model generates output tokens.
                </li>
                <li>
                  <strong className="text-[var(--text-primary)]">
                    Draft acceptance
                  </strong>{' '}
                  — how often the speculative-decoding draft tokens were accepted
                  by the main model. Higher means the speculation is more effective.
                </li>
              </ul>
              <p>
                These are session averages, not instantaneous readings. The raw
                per-poll values are plotted in the Token Throughput chart below.
              </p>
            </>
          }
        />
      </div>

      {/* Secondary counters row */}
      <div className="grid grid-cols-2 gap-4 text-sm text-[var(--text-secondary)] sm:grid-cols-4">
        <div>
          <span className="text-[var(--text-muted)]">Prompt tokens total: </span>
          {fmtInt(halogen.prompt_tokens_total)}
        </div>
        <div>
          <span className="text-[var(--text-muted)]">Generated tokens total: </span>
          {fmtInt(halogen.tokens_predicted_total)}
        </div>
        <div>
          <span className="text-[var(--text-muted)]">Cached prompt tokens: </span>
          {fmtInt(halogen.prompt_tokens_cached_total)}
        </div>
        <div>
          <span className="text-[var(--text-muted)]">CPU: </span>
          {fmtPct(cpu.usage_pct)} · {fmtInt(cpu.active_threads)} threads
        </div>
      </div>

      {/* Session statistics (min / avg / max since startup) */}
      <StatsSummary stats={data?.stats} onReset={onResetStats} />

      {/* Dynamic charts */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <ThroughputChart history={history} colors={colors} />
        <MemoryChart history={history} colors={colors} />
      </div>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <KvPoolChart history={history} colors={colors} />
        <QueueChart history={history} colors={colors} />
      </div>
    </div>
  )
}
