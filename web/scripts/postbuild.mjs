/**
 * Make the built app survive a dumb static server.
 *
 * The router uses real paths (/plan) rather than hashes, which needs the
 * server to fall back to index.html for unknown routes. `vite preview` does
 * that; `python -m http.server` does not, and a 404 on a deep link is a
 * miserable way to discover the difference. Writing the same shell to
 * /plan/index.html makes every route load correctly anywhere.
 */
import { copyFile, mkdir } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const dist = resolve(dirname(fileURLToPath(import.meta.url)), "..", "dist");
const ROUTES = ["plan", "routes"];

for (const route of ROUTES) {
  await mkdir(resolve(dist, route), { recursive: true });
  await copyFile(resolve(dist, "index.html"), resolve(dist, route, "index.html"));
}

console.log(`Wrote static fallbacks for: ${ROUTES.map((r) => `/${r}`).join(", ")}`);
