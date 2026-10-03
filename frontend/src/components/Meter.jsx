export default function Meter({ pct, color = 'var(--series-1)' }) {
  const clamped = Math.max(0, Math.min(100, pct ?? 0))
  return (
    <div
      className="h-2 w-full overflow-hidden rounded-full bg-[var(--grid-line)]"
      role="progressbar"
      aria-valuenow={Math.round(clamped)}
      aria-valuemin={0}
      aria-valuemax={100}
    >
      <div
        className="h-full rounded-full transition-all duration-500"
        style={{ width: `${clamped}%`, backgroundColor: color }}
      />
    </div>
  )
}
