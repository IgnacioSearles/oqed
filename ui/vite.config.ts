import path from "node:path";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import { runsPlugin } from "./runs-plugin";

export default defineConfig({
  base: "./",
  plugins: [react(), tailwindcss(), runsPlugin(path.resolve(__dirname, "../runs"))],
});
