// Chart color tokens. Hex values (not CSS vars) because Recharts writes SVG
// presentation attributes, which do not resolve var().
// Both modes validated with the dataviz palette validator:
//   light surface #fcfcfb, dark surface #1a1a19 -- all checks pass.

export const CHART = {
  light: {
    surface: '#fcfcfb',
    grid: '#e1e0d9',
    axis: '#c3c2b7',
    muted: '#898781',
    text: '#0b0b0b',
    textSecondary: '#52514e',
    blue: '#2a78d6',
    orange: '#eb6834',
    aqua: '#1baf7a',
    violet: '#4a3aa7',
  },
  dark: {
    surface: '#1a1a19',
    grid: '#2c2c2a',
    axis: '#383835',
    muted: '#898781',
    text: '#ffffff',
    textSecondary: '#c3c2b7',
    blue: '#3987e5',
    orange: '#d95926',
    aqua: '#199e70',
    violet: '#9085e9',
  },
}

export const STATUS_COLORS = {
  PASS: '#0ca30c',
  WARN: '#fab219',
  FAIL: '#d03b3b',
}
