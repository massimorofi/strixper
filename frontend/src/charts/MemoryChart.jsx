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

// Chart C -- host memory + GPU utilization. GTT (GPU unified pool), system RAM,
// VRAM (via rocm-smi) and GPU compute utilization, all as percent-of-total so
// one 0-100% axis is honest.
export default function MemoryChart({ history, colors }) {
  return (
    <ChartCard
      title="Host Memory & GPU Utilization"
      subtitle="GTT / RAM / VRAM usage and GPU compute utilization"
      info={
        <>
          <p>
            Host Memory & GPU Utilization tracks the four key resource pressures
            on the machine, each as a percentage of that resource's total, so they
            share one 0–100% axis.
          </p>
          <ul className="list-disc space-y-1.5 pl-5">
            <li>
              <strong className="text-[var(--text-primary)]">GTT used</strong> —
              the GPU's unified memory pool (Graphics Translation Table), read from
              amdgpu sysfs. On Strix Halo this is the large pool the model
              actually runs in.
            </li>
            <li>
              <strong className="text-[var(--text-primary)]">RAM used</strong> —
              system memory usage from the OS: OS overhead, page cache, and the
              model's memory-mapped weights.
            </li>
            <li>
              <strong className="text-[var(--text-primary)]">VRAM used</strong> —
              the GPU's dedicated VRAM, read via rocm-smi. This is a smaller
              carve-out than GTT, so it fills faster and reads higher.
            </li>
            <li>
              <strong className="text-[var(--text-primary)]">GPU util</strong> —
              GPU compute utilization: how busy the GPU cores are, via rocm-smi.
              Near 99% during active generation is expected and good.
            </li>
          </ul>
          <p>
            rocm-smi reports higher VRAM usage than raw sysfs because it counts
            driver-reserved allocations that sysfs leaves out.
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
            domain={[0, 100]}
            stroke={colors.axis}
            tick={{ fill: colors.muted, fontSize: 11 }}
            tickFormatter={(v) => `${v}%`}
            width={48}
          />
          <Tooltip content={<ChartTooltip unit="%" />} />
          <Legend
            wrapperStyle={{ fontSize: 12, color: colors.textSecondary }}
            iconType="plainline"
          />
          <Line
            type="monotone"
            dataKey="gttPct"
            name="GTT used"
            stroke={colors.blue}
            strokeWidth={2}
            dot={false}
            isAnimationActive={false}
          />
          <Line
            type="monotone"
            dataKey="ramPct"
            name="RAM used"
            stroke={colors.orange}
            strokeWidth={2}
            dot={false}
            isAnimationActive={false}
          />
          <Line
            type="monotone"
            dataKey="vramPct"
            name="VRAM used"
            stroke={colors.aqua}
            strokeWidth={2}
            dot={false}
            isAnimationActive={false}
          />
          <Line
            type="monotone"
            dataKey="gpuUtilPct"
            name="GPU util"
            stroke={colors.violet}
            strokeWidth={2}
            dot={false}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </ChartCard>
  )
}
