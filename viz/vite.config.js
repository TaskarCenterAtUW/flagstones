import { defineConfig } from "vite";

// The app fetches the bundles in data/ at runtime, so a build leaves them out
// and a preview of dist/ needs them copied in.
export default defineConfig({
  server: { port: 5173, strictPort: true },
  build: { target: "es2022", sourcemap: true },
});
