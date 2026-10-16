import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { applyTheme, getInitialTheme } from './lib/theme.ts'

// Applied before the first render, not inside a React effect -- an
// effect would paint the wrong theme for one frame first (a real,
// visible flash), the exact thing synchronous application here avoids.
applyTheme(getInitialTheme())

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
