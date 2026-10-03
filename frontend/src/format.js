export const fmtInt = (v) => (v == null ? '—' : Number(v).toLocaleString('en-US'))

export const fmtNum = (v, d = 1) => (v == null ? '—' : Number(v).toFixed(d))

export const fmtPct = (v, d = 1) => (v == null ? '—' : `${Number(v).toFixed(d)}%`)

export function fmtTime(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleTimeString('en-GB')
}
