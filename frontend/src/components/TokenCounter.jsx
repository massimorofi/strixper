import { useState } from 'react'
import { Calculator, Loader2 } from 'lucide-react'
import { fmtInt } from '../format.js'
import { countTokens } from '../api.js'

// Count the token cost of a prompt without generating, via the backend's
// /api/v1/count-tokens proxy (Halogen /v1/messages/count_tokens).
export default function TokenCounter({ model }) {
  const [text, setText] = useState('')
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  const handleCount = async () => {
    if (!text.trim()) return
    setBusy(true)
    setError(null)
    try {
      const res = await countTokens(text)
      setResult(res)
    } catch (err) {
      setError(err?.message || 'Count failed')
      setResult(null)
    } finally {
      setBusy(false)
    }
  }

  const handleKeyDown = (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
      e.preventDefault()
      handleCount()
    }
  }

  return (
    <div className="rounded-xl border border-[var(--border-hairline)] bg-[var(--surface-card)] p-4">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-[var(--text-primary)]">
            Token Counter
          </h2>
          <p className="mt-0.5 text-xs text-[var(--text-muted)]">
            Count the tokens a prompt will cost · {model || 'no model'}
          </p>
        </div>
      </div>

      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={handleKeyDown}
        rows={4}
        placeholder="Paste your prompt here…"
        className="mt-3 w-full resize-y rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-page)] px-3 py-2 text-sm text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:border-[var(--series-1)] focus:outline-none"
      />

      <div className="mt-2 flex items-center justify-between gap-3">
        <div className="text-xs text-[var(--text-muted)]">
          {fmtInt(text.length)} characters
        </div>
        <button
          type="button"
          onClick={handleCount}
          disabled={busy || !text.trim()}
          className="inline-flex items-center gap-1.5 rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-page)] px-3 py-1.5 text-xs font-medium text-[var(--text-secondary)] transition-colors hover:border-[var(--series-1)] hover:text-[var(--text-primary)] disabled:cursor-not-allowed disabled:opacity-50"
        >
          {busy ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <Calculator className="h-3.5 w-3.5" />
          )}
          {busy ? 'Counting…' : 'Count tokens'}
        </button>
      </div>

      {error && (
        <div className="mt-2 rounded-lg border border-[var(--status-critical)] px-3 py-2 text-xs text-[var(--status-critical)]">
          {error}
        </div>
      )}

      {result && !error && (
        <div className="mt-3 grid grid-cols-2 gap-x-6 gap-y-2 sm:grid-cols-3">
          <div>
            <div className="text-xs text-[var(--text-muted)]">Input tokens</div>
            <div className="text-lg font-semibold tabular-nums text-[var(--text-primary)]">
              {fmtInt(result.input_tokens)}
            </div>
          </div>
          <div>
            <div className="text-xs text-[var(--text-muted)]">Characters</div>
            <div className="text-sm font-medium tabular-nums text-[var(--text-primary)]">
              {fmtInt(result.chars)}
            </div>
          </div>
          <div>
            <div className="text-xs text-[var(--text-muted)]">Chars / token</div>
            <div className="text-sm font-medium tabular-nums text-[var(--text-primary)]">
              {result.input_tokens > 0
                ? (result.chars / result.input_tokens).toFixed(2)
                : '—'}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
