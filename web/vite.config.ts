import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../src/kanban_agent_orchestrator/static",
    emptyOutDir: true,
  },
});
