// Client-side mirror of backend/app/services/params.py.
//
// A run configuration stores its docker command as a template with
// `{parameter_name}` placeholders, plus the list of parameters that supply
// the values. The backend renders the real command at run time; these
// helpers let the form validate and preview the same thing while editing.
// The backend stays the authority -- it re-validates on save and at run
// time -- so this is for immediate feedback, not for trust.

// Identifier-shaped names only: letters, digits, underscore, not leading digit.
export const PARAM_NAME_RE = /^[A-Za-z_][A-Za-z0-9_]*$/

// Same shape, but found anywhere inside the command template.
const PLACEHOLDER_RE = /\{([A-Za-z_][A-Za-z0-9_]*)\}/g

export const MAX_PARAMS = 40

/** Placeholder names in a template, in order of first appearance. */
export function findPlaceholders(command) {
  if (!command) return []
  const found = []
  for (const m of command.matchAll(PLACEHOLDER_RE)) {
    if (!found.includes(m[1])) found.push(m[1])
  }
  return found
}

/** Placeholders the parameter list cannot fill. */
export function missingParameters(command, params = []) {
  const names = new Set(params.map((p) => p.name))
  return findPlaceholders(command).filter((p) => !names.has(p))
}

/** Defined parameters the template never references. */
export function unusedParameters(command, params = []) {
  const used = new Set(findPlaceholders(command))
  return params.map((p) => p.name).filter((n) => !used.has(n))
}

/**
 * Substitute every `{name}` with its parameter value. Blank values are
 * reported rather than substituted, so a half-filled template never looks
 * complete.
 */
export function renderTemplate(command, params = []) {
  const values = Object.fromEntries(params.map((p) => [p.name, p.value ?? '']))
  const blanks = findPlaceholders(command).filter(
    (n) => !String(values[n] ?? '').trim(),
  )
  if (blanks.length) {
    throw new Error(`no value supplied for ${blanks.map((n) => `{${n}}`).join(', ')}`)
  }
  return command.replace(PLACEHOLDER_RE, (_, name) => values[name])
}

/**
 * Per-row validation for the parameter editor.
 * Returns an array aligned with `params`: null when the row is fine,
 * otherwise a short message.
 */
export function validateRows(params = []) {
  const seen = new Map()
  return params.map((p, i) => {
    const name = (p.name || '').trim()
    if (!name) return 'Name is required'
    if (!PARAM_NAME_RE.test(name))
      return 'Letters, digits and underscore only; cannot start with a digit'
    const key = name.toLowerCase()
    if (seen.has(key)) return `Same as parameter #${seen.get(key) + 1}`
    seen.set(key, i)
    return null
  })
}

/** Normalise a stored/edited parameter list into `{name, label, value}` rows. */
export function normaliseRows(parameters) {
  if (!Array.isArray(parameters)) return []
  return parameters.map((p) => ({
    name: p?.name ?? '',
    label: p?.label ?? '',
    value: p?.value ?? '',
  }))
}
