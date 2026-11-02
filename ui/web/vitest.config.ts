import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Component tests exercise local API interactions without a browser automation service.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test-setup.ts"],
    clearMocks: true,
  },
});
