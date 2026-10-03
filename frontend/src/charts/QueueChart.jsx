import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { fmtTime } from '../format.js'
import ChartCard from './ChartCard.jsx'
import ChartTooltip from './ChartTooltip.jsx'

// Queue depth over time. Kept as its own chart (not overlaid on the KV area)
// because request-count and cache-percent are different units -- one axis only.
export default function QueueChart({ history, colors }) {
  return (
    <ChartCard
      title="Queue Depth"
      subtitle="Requests waiting for a free slot"
      info={
        <>
          <p>
            Queue Depth is the number of incoming requests waiting for a free
            processing slot on the engine.
          </p>
          <ul className="list-disc space-y-1.5 pl-5">
            <li>
              <strong className="text-[var(--text-primary)]">0</strong> — the
              engine keeps up; every request gets a slot immediately. This is the
              normal, healthy state for single-user load.
            </li>
            <li>
              <strong className="text-[var(--text-primary)]">Rising</strong> —
              more requests have arrived than there are free slots. Sustained
              queueing means requests are waiting and latency is climbing.
            </li>
          </ul>
          <p>
            Queue depth only becomes non-zero under concurrent load that exceeds
            the engine's slot count.
          </p>
        </>
      }
    >
      <ResponsiveContainer width="100%" height={220}>
        <LineChart data={history} margin={{ top: 8, right: 12, bottom: 4, left: 4 }}>
          <CartesianGrid stroke={colors.grid} vertical={false} />
          <XAxis
            dataKey="time"
            tickFormatter={fmtTime}
            stroke={colors.axis}
            tick={{ fill: colors.muted, fontSize: 11 }}
            minTickGap={48}
          />
          <YAxis
            stroke={colors.axis}
            tick={{ fill: colors.muted, fontSize: 11 }}
            allowDecimals={false}
            width={40}
          />
          <Tooltip content={<ChartTooltip unit=" req" decimals={0} />} />
          <Line
            type="stepAfter"
            dataKey="queued"
            name="Queued"
            stroke={colors.orange}
            strokeWidth={2}
            dot={false}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </ChartCard>
  )
}
