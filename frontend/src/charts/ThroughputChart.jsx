import {
  CartesianGrid,
  Legend,
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

// Chart A -- dual-line token throughput. Both series share one unit (t/s),
// so a single Y axis is honest.
export default function ThroughputChart({ history, colors }) {
  return (
    <ChartCard
      title="Token Throughput"
      subtitle="Prompt processing vs decode generation (tokens/s)"
      info={
        <>
          <p>
            Token throughput measures how fast the engine moves text, in tokens
            per second (t/s). Both lines share this one unit, so they share one
            axis.
          </p>
          <ul className="list-disc space-y-1.5 pl-5">
            <li>
              <strong className="text-[var(--text-primary)]">Prompt (t/s)</strong>{' '}
              — prefill speed: how fast the model reads and encodes your input.
              This is a single parallel pass over the whole prompt, so it runs
              high (hundreds to ~1000 t/s).
            </li>
            <li>
              <strong className="text-[var(--text-primary)]">Decode (t/s)</strong>{' '}
              — generation speed: how fast the model writes each new token, one at
              a time. This is the rate that governs how quickly text appears on
              screen.
            </li>
          </ul>
          <p>
            Halogen commits these figures when a request completes, so a reading
            of 0 means no completed activity has been reported yet — not a
            measured zero. The Session Statistics box ignores zeros in its average
            and minimum.
          </p>
        </>
      }
    >
      <ResponsiveContainer width="100%" height={260}>
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
            tickFormatter={(v) => `${v}`}
            width={56}
            label={{
              value: 't/s',
              angle: -90,
              position: 'insideLeft',
              fill: colors.muted,
              fontSize: 11,
            }}
          />
          <Tooltip content={<ChartTooltip unit=" t/s" />} />
          <Legend
            wrapperStyle={{ fontSize: 12, color: colors.textSecondary }}
            iconType="plainline"
          />
          <Line
            type="monotone"
            dataKey="promptTps"
            name="Prompt (t/s)"
            stroke={colors.blue}
            strokeWidth={2}
            dot={false}
            isAnimationActive={false}
          />
          <Line
            type="monotone"
            dataKey="decodeTps"
            name="Decode (t/s)"
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
