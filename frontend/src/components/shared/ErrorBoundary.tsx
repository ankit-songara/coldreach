import { Component, type ReactNode } from 'react'

/**
 * Last-resort guard: a render error anywhere below unmounts React's tree and
 * leaves a blank white page. This catches it and shows a friendly recovery
 * screen instead. Details go to the console for debugging, never to the user.
 */
export default class ErrorBoundary extends Component<
  { children: ReactNode },
  { hasError: boolean }
> {
  state = { hasError: false }

  static getDerivedStateFromError() {
    return { hasError: true }
  }

  componentDidCatch(error: unknown, info: unknown) {
    console.error('ColdReach render error:', error, info)
  }

  render() {
    if (!this.state.hasError) return this.props.children
    return (
      <div
        className="min-h-screen flex items-center justify-center px-6"
        style={{ background: 'var(--bg, #faf7f2)' }}
      >
        <div className="text-center max-w-sm">
          <div style={{ fontSize: 40, marginBottom: 12 }}>😵</div>
          <h1 style={{ fontSize: 20, fontWeight: 800, color: 'var(--text, #241d17)', marginBottom: 8 }}>
            Something went wrong
          </h1>
          <p style={{ fontSize: 14, color: 'var(--text-muted, #6f6457)', lineHeight: 1.6, marginBottom: 20 }}>
            An unexpected error interrupted the page. Your data is safe —
            reloading usually fixes it.
          </p>
          <button
            onClick={() => window.location.reload()}
            style={{
              padding: '10px 24px', borderRadius: 999, border: 'none', cursor: 'pointer',
              background: 'var(--accent, #e2603f)', color: '#fff', fontWeight: 700, fontSize: 14,
            }}
          >
            Reload ColdReach
          </button>
        </div>
      </div>
    )
  }
}
