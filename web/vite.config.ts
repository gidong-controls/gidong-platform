import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const developmentHost = "http://127.0.0.1:8767";
const developmentWebOrigins = new Set([
  "http://127.0.0.1:5173",
  "http://localhost:5173",
]);

export default defineConfig({
  plugins: [
    react(),
    {
      name: "controlstudio-classic-production-entry",
      enforce: "post",
      transformIndexHtml: {
        order: "post",
        handler(html, context) {
          if (!context.bundle) {
            return html;
          }

          // The ControlStudio Host serves one self-contained production
          // bundle. Its hardened local-app browser surface does not execute
          // the generated module entry, while the same bundle executes as a
          // deferred classic script. Keep Vite's module entry in development
          // and rewrite only the finalized production HTML.
          return html
            .replace(
              /<script type="module" crossorigin src="(\/assets\/index-[^"]+\.js)"><\/script>/,
              '<script defer src="$1"></script>',
            )
            .replace(
              /<link rel="stylesheet" crossorigin href="(\/assets\/index-[^"]+\.css)">/,
              '<link rel="stylesheet" href="$1">',
            );
        },
      },
    },
  ],
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": {
        target: developmentHost,
        changeOrigin: true,
        configure(proxy) {
          proxy.on("proxyReq", (forwarded, request) => {
            // Only this local development page may use the proxy's Host
            // origin. Keep unrelated origins intact for the Host to reject.
            if (typeof request.headers.origin === "string"
                && developmentWebOrigins.has(request.headers.origin)) {
              forwarded.setHeader("Origin", developmentHost);
            }
          });
        },
      },
    },
  },
  preview: {
    host: "127.0.0.1",
    port: 4173,
    strictPort: true,
  },
  build: {
    sourcemap: true,
  },
});
