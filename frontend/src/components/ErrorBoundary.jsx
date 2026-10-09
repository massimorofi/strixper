import React from 'react'

// Without a boundary in the tree, a single render error makes React unmount
// everything: the page goes to a blank dark screen that looks like the whole
// dashboard died, when in fact one value in one component was unexpected.
// This keeps the rest of the page alive and shows what actually went wrong.
export default class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props)
    this.state = { error: null }
  }

  static getDerivedStateFromError(error) {
    return { error }
  }

  componentDidCatch(error, info) {
    console.error('[ErrorBoundary]', error, info?.componentStack)
  }

  render() {
    if (this.state.error) {
      const message =
        typeof this.state.error?.message === 'string' && this.state.error.message
          ? this.state.error.message
          : 'Unexpected error'
      return (
        <div className="mt-4 rounded-xl border border-[var(--status-critical)] bg-[var(--surface-card)] px-4 py-3">
          <h2 className="text-sm font-semibold text-[var(--status-critical)]">
            This view could not be rendered
          </h2>
          <p className="mt-1 break-words text-xs leading-relaxed text-[var(--text-secondary)]">
            {message}
          </p>
          <p className="mt-1 text-xs text-[var(--text-muted)]">
            The rest of the dashboard is still working. You can switch tabs above,
            or dismiss this to try rendering it again.
          </p>
          <button
            type="button"
            onClick={() => this.setState({ error: null })}
            className="mt-3 rounded-md border border-[var(--border-hairline)] px-3 py-1.5 text-xs text-[var(--text-secondary)] transition-colors hover:bg-[var(--surface-page)] hover:text-[var(--text-primary)]"
          >
            Dismiss
          </button>
        </div>
      )
    }
    return this.props.children
  }
}
