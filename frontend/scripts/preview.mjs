import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const port = String(process.env.PORT || "4173");
const vite = path.join(root, "node_modules", "vite", "bin", "vite.js");
const child = spawn(process.execPath, [vite, "preview", "--host", "0.0.0.0", "--port", port], {
  cwd: root,
  stdio: "inherit",
});

child.on("exit", (code, signal) => {
  if (signal) {
    process.kill(process.pid, signal);
    return;
  }
  process.exit(code ?? 1);
});
