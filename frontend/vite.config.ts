import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // Pinned rather than left to Vite's 5173 default, which collides with
    // every other Vite project on the machine. The whole stack uses one
    // contiguous block (7590 API, 7591 UI, 7592 Postgres, 7593 Qdrant),
    // unassigned in /etc/services and clear of the crowded defaults.
    port: 7591,
    // Fail loudly instead of drifting to the next free port. Vite's default
    // behaviour is to step to 5174 with only a line of log output, which is
    // how a stale server on the pinned port once left the UI talking to
    // nothing: the page loaded, every request was refused, and the sidebar
    // simply read "No filings yet". A refusal to start is a better failure -
    // it says what is wrong at the moment it goes wrong.
    strictPort: true,
  },
})
