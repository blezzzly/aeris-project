import { Component } from 'react';
import type { ErrorInfo, ReactNode } from 'react';

interface ErrorBoundaryProps {
  children: ReactNode;
}

interface ErrorBoundaryState {
  hasError: boolean;
  error: Error | null;
}

export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { hasError: false, error: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error('AERIS Dashboard crashed:', error, errorInfo.componentStack);
  }

  handleReload = () => {
    window.location.reload();
  };

  render() {
    if (this.state.hasError) {
      return (
        <div className="h-screen w-screen flex items-center justify-center bg-slate-100">
          <div className="panel-card max-w-md text-center">
            <h2 className="text-lg font-bold text-slate-800 mb-2">Dashboard Unavailable</h2>
            <p className="text-sm text-slate-500 mb-4">
              The AERIS dashboard hit an unexpected error.
            </p>
            <button
              onClick={this.handleReload}
              className="w-full py-2 bg-aeris hover:bg-aeris-dark text-white font-semibold rounded-lg transition-colors"
            >
              Reload Dashboard
            </button>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}
