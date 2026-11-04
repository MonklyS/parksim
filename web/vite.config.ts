import { createHash } from "node:crypto";
import { readdirSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { defineConfig, type Plugin } from "vite";

// The browser runs the real Python engine (../parksim) with Pyodide. This plugin serves
// its .py files at /py/parksim/ during development and copies them into the build,
// so the browser always runs exactly the same code as the server.
const ENGINE_DIR = fileURLToPath(new URL("../parksim/", import.meta.url));
const SKIP = new Set(["__main__.py", "cli.py"]); // command-line only

function engineManifest() {
  const files = readdirSync(ENGINE_DIR)
    .filter((f) => f.endsWith(".py") && !SKIP.has(f))
    .sort();
  const hash = createHash("sha256");
  for (const f of files) hash.update(f).update(readFileSync(ENGINE_DIR + f));
  return { version: hash.digest("hex").slice(0, 12), files };
}

function parksimEngine(): Plugin {
  return {
    name: "parksim-engine",
    configureServer(server) {
      server.middlewares.use("/py/parksim/", (req, res, next) => {
        const name = (req.url ?? "").slice(1).split("?")[0];
        const manifest = engineManifest();
        if (name === "manifest.json") {
          res.setHeader("Content-Type", "application/json");
          res.end(JSON.stringify(manifest));
        } else if (manifest.files.includes(name)) {
          res.setHeader("Content-Type", "text/plain; charset=utf-8");
          res.end(readFileSync(ENGINE_DIR + name));
        } else next();
      });
    },
    generateBundle() {
      const manifest = engineManifest();
      this.emitFile({
        type: "asset",
        fileName: "py/parksim/manifest.json",
        source: JSON.stringify(manifest),
      });
      for (const f of manifest.files)
        this.emitFile({
          type: "asset",
          fileName: `py/parksim/${f}`,
          source: readFileSync(ENGINE_DIR + f, "utf8"),
        });
    },
  };
}

// In development the Python API runs on :8000 and Vite proxies /api (including the
// live-simulation WebSocket) to it. In production FastAPI serves the built files itself.
export default defineConfig({
  plugins: [parksimEngine()],
  worker: { format: "es" },
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", ws: true },
    },
  },
});
