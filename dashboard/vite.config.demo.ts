// Used ONLY by ops/demo/start.sh, never by `make dashboard-*` or CI.
// dashboard/vite.config.ts hard-codes its dev proxy target at
// 127.0.0.1:8000 -- the right default for the one real dev stack, but
// this local demo's own API container listens on a distinct host port
// (127.0.0.1:8010, see ops/demo/docker-compose.override.yml's own
// comment for why) so it can run ALONGSIDE that stack without a port
// clash. mergeConfig() layers just the proxy target over the real
// config instead of forking it, so every other setting (plugins, the
// '@' alias, the vitest block) stays byte-identical to what `npm run
// dev` itself uses.
import { mergeConfig } from 'vite'
import base from './vite.config'

export default mergeConfig(base, {
  server: {
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8010',
        changeOrigin: true,
      },
    },
  },
})
