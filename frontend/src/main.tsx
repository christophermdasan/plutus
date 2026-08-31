import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import '@fontsource/inter/400.css'
import '@fontsource/inter/500.css'
import '@fontsource/inter/600.css'
import '@fontsource/jetbrains-mono/400.css'
import '@fontsource/jetbrains-mono/500.css'
import './index.css'
import App from './App.tsx'
import { setToken } from './lib/api'

// Authentication is intentionally absent from this local analyst workspace.
// Clear a token left by an older build so API requests and document iframes
// always address the same guest workspace.
setToken(null)

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
