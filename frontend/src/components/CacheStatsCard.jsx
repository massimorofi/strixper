import { useState } from 'react'
import { Info } from 'lucide-react'
import { fmtInt, fmtNum, fmtPct } from '../format.js'
import InfoModal from '../charts/InfoModal.jsx'

// Halogen prompt-cache telemetry card, fed by GET /cache (surfaced in the
// `halogen.cache` section of live-status).
export default function CacheStatsCard({ cache }) {
  const [showInfo, setShowInfo] = useState(false)

  const c = cache || {}
  const pool = c.pool || {}
  const disk = c.disk || {}

  const hitRate = c.hit_rate != null ? fmtPct(c.hit_rate * 100) : '—'
  const tokenHitRate = c.token_hit_rate != null ? fmtPct(c.token_hit_rate * 100) : '—'
  const poolPct = pool.usage_ratio != null ? fmtPct(pool.usage_ratio * 100) : '—'
  const entries =
    c.entries != null && c.max_entries != null
      ? `${fmtInt(c.entries)} / ${fmtInt(c.max_entries)}`
      : '—'

  const items = [
    { label: 'Request hit rate', value: hitRate },
    { label: 'Token hit rate', value: tokenHitRate },
    { label: 'Prompt tokens saved', value: fmtInt(c.prompt_tokens_saved) },
    { label: 'Cache entries', value: entries },
    { label: 'Hits', value: fmtInt(c.hits) },
    { label: 'Misses', value: fmtInt(c.misses) },
    { label: 'Stores', value: fmtInt(c.stores) },
    { label: 'Evicted', value: fmtInt(c.evicted) },
    { label: 'Pool usage', value: poolPct },
    { label: 'Store latency (total)', value: `${fmtNum(c.store_ms_total, 0)} ms` },
    { label: 'Restore latency (total)', value: `${fmtNum(c.restore_ms_total, 0)} ms` },
    { label: 'Refused stores', value: fmtInt(c.refused) },
  ]

  return (
    <div className="rounded-xl border border-[var(--border-hairline)] bg-[var(--surface-card)] p-4">
      <div className="flex items-start justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold text-[var(--text-primary)]">
            Prompt Cache
          </h2>
          <p className="mt-0.5 text-xs text-[var(--text-muted)]">
            Halogen prefix-cache performance ·{' '}
            {disk.on ? 'disk tier ON' : 'disk tier OFF'}
          </p>
        </div>
        <button
          type="button"
          onClick={() => setShowInfo(true)}
          aria-label="About Prompt Cache"
          title="What this measure means"
          className="-mr-1 -mt-1 shrink-0 rounded-md p-1 text-[var(--text-muted)] transition-colors hover:bg-[var(--surface-page)] hover:text-[var(--text-primary)]"
        >
          <Info className="h-3.5 w-3.5" />
        </button>
      </div>

      <div className="mt-3 grid grid-cols-2 gap-x-6 gap-y-2 sm:grid-cols-3">
        {items.map((item) => (
          <div key={item.label}>
            <div className="text-xs text-[var(--text-muted)]">{item.label}</div>
            <div className="text-sm font-medium tabular-nums text-[var(--text-primary)]">
              {item.value}
            </div>
          </div>
        ))}
      </div>

      {showInfo && (
        <InfoModal title="Prompt Cache" onClose={() => setShowInfo(false)}>
          <p>
            Halogen caches the KV state of prompt prefixes so repeated or shared
            prompt prefixes skip re-prefilling. These counters come from the{' '}
            <code className="text-[var(--text-primary)]">GET /cache</code>{' '}
            endpoint.
          </p>
          <ul className="list-disc space-y-1.5 pl-5">
            <li>
              <strong className="text-[var(--text-primary)]">
                Request hit rate
              </strong>{' '}
              — fraction of requests that reused cached prefix state (server
              computed).
            </li>
            <li>
              <strong className="text-[var(--text-primary)]">
                Token hit rate
              </strong>{' '}
              — fraction of prompt tokens covered by the cache.
            </li>
            <li>
              <strong className="text-[var(--text-primary)]">
                Prompt tokens saved
              </strong>{' '}
              — total prompt tokens not re-processed thanks to cache hits.
            </li>
            <li>
              <strong className="text-[var(--text-primary)]">Cache entries</strong>{' '}
              — live snapshots vs. the configured maximum.
            </li>
            <li>
              <strong className="text-[var(--text-primary)]">
                Store / Restore latency
              </strong>{' '}
              — cumulative time writing to and reading from the cache.
            </li>
            <li>
              <strong className="text-[var(--text-primary)]">Refused stores</strong>{' '}
              — cache writes the engine declined (e.g. no room).
            </li>
          </ul>
          <p>
            Higher hit rates mean faster time-to-first-token and less GPU work per
            request.
          </p>
        </InfoModal>
      )}
    </div>
  )
}
