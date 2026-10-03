import { AlertTriangle, CheckCircle2, XCircle } from 'lucide-react'
import { STATUS_COLORS } from '../theme.js'

const ICONS = {
  PASS: CheckCircle2,
  WARN: AlertTriangle,
  FAIL: XCircle,
}

// Icon + label always travel together: the color never carries meaning alone.
export default function StatusBadge({ status }) {
  const Icon = ICONS[status] || AlertTriangle
  const color = STATUS_COLORS[status] || STATUS_COLORS.WARN
  return (
    <span className="inline-flex items-center gap-1.5 text-sm font-medium text-[var(--text-primary)]">
      <Icon size={16} style={{ color }} />
      {status}
    </span>
  )
}
