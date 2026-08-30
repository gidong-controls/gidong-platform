import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

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
