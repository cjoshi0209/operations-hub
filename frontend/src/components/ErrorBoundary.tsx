import { Component, ReactNode } from "react";

/** Last line of defence: a render bug shows a recoverable message instead of a blank page. */
export default class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error) {
    console.error("Unhandled UI error", error);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="page">
        <div className="card stack" role="alert">
          <h1>Something went wrong</h1>
          <p className="muted">The page hit an unexpected error. Your data is safe — nothing was changed by this.</p>
          <div className="actions">
            <button className="primary" onClick={() => window.location.reload()}>
              Reload
            </button>
            <button onClick={() => (window.location.href = "/")}>Back to overview</button>
          </div>
        </div>
      </div>
    );
  }
}
