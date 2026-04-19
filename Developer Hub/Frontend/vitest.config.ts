/// <reference types="vitest" />
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "path";

export default defineConfig({
    plugins: [react()],
    test: {
        environment: "jsdom",
        globals: true,
        setupFiles: ["./tests/setup.ts"],
        include: ["tests/**/*.test.{ts,tsx}"],
        css: false,
    },
    resolve: {
        alias: {
            // Ensure a single React copy so RTL works with Fluent UI.
            react: path.resolve(__dirname, "node_modules/react"),
            "react-dom": path.resolve(__dirname, "node_modules/react-dom"),
        },
    },
});
