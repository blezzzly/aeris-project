import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { ErrorBoundary } from './components/ErrorBoundary'
import { useAppStore } from './stores/appStore'

// Expose the store for diagnostics: the live hardware state can be inspected
// from the browser console (e.g. `__aerisStore.getState().devices`).
;(window as unknown as { __aerisStore: typeof useAppStore }).__aerisStore = useAppStore

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </StrictMode>,
)
