import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
const contractsPath = decodeURIComponent(new URL("../../packages/contracts/src/index.ts", import.meta.url).pathname).replace(/^\/([A-Za-z]:\/)/, "$1");

export default defineConfig({
  clearScreen: false,
  plugins: [react()],
  resolve: { alias: { "@content-factory/contracts": contractsPath } },
  server: {
    host: "127.0.0.1",
    port: 1420,
    strictPort: true,
    watch: {
      ignored: ["**/src-tauri/**"],
    },
  },
});
