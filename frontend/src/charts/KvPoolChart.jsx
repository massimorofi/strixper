import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { fmtTime } from '../format.js'
import ChartCard from './ChartCard.jsx'
import ChartTooltip from './ChartTooltip.jsx'

// KV cache position utilization over time (single series, 0-100%).
export default function KvPoolChart({ history, colors }) {
  return (
    <ChartCard
      title="KV Pool Utilization"
      subtitle="Cache positions held over pool capacity"
      info={
        <>
          <p>
            KV Pool Utilization shows how much of the model's KV (key-value) cache
            is currently occupied, as a percentage of total pool capacity.
          </p>
          <p>
            The KV cache stores the attention state computed for every token in
            the active context. It grows as the conversation gets longer and is
            the main GPU-memory consumer during generation.
          </p>
          <ul className="list-disc space-y-1.5 pl-5">
            <li>
              <strong className="text-[var(--text-primary)]">Low %</strong> —
              plenty of room; long contexts and several concurrent requests are
              fine.
            </li>
            <li>
              <strong className="text-[var(--text-primary)]">
                High % (near 100%)
              </strong>{' '}
              — the context window is nearly full. New requests may be deferred,
              older context may be evicted, and you may hit context-length limits.
            </li>
          </ul>
        </>
      }
    >
      <ResponsiveContainer width="100%" height={220}>
        <AreaChart data={history} margin={{ top: 8, right: 12, bottom: 4, left: 4 }}>
          <defs>
            <linearGradient id="kvFill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={colors.blue} stopOpacity={0.35} />
              <stop offset="100%" stopColor={colors.blue} stopOpacity={0.05} />
            </linearGradient>
          </defs>
          <CartesianGrid stroke={colors.grid} vertical={false} />
          <XAxis
            dataKey="time"
            tickFormatter={fmtTime}
            stroke={colors.axis}
            tick={{ fill: colors.muted, fontSize: 11 }}
            minTickGap={48}
          />
          <YAxis
            domain={[0, 100]}
            stroke={colors.axis}
            tick={{ fill: colors.muted, fontSize: 11 }}
            tickFormatter={(v) => `${v}%`}
            width={48}
          />
          <Tooltip content={<ChartTooltip unit="%" />} />
          <Area
            type="monotone"
            dataKey="kvPct"
            name="KV used"
            stroke={colors.blue}
            strokeWidth={2}
            fill="url(#kvFill)"
            dot={false}
            isAnimationActive={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </ChartCard>
  )
}
