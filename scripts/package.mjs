// Build a releasable app, in the one order that works.
//
// The interface is built first because the core carries it, and the runtime is
// assembled after that because it carries the core. Doing any of these by hand
// is fine too; this exists so a release is one command and the order cannot be
// remembered wrongly.
//
// Usage: node scripts/package.mjs
//        node scripts/package.mjs --dir   (unpacked app, no installer)

import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const unpacked = process.argv.includes("--dir");

function run(command, args, cwd) {
  console.log(`\n> ${command} ${args.join(" ")}`);
  const result = spawnSync(command, args, { cwd, stdio: "inherit", shell: process.platform === "win32" });
  if (result.status !== 0) {
    throw new Error(`${command} ${args.join(" ")} exited with ${result.status}`);
  }
}

function main() {
  run("npm", ["run", "build"], path.join(repoRoot, "web"));
  run("node", ["scripts/build-web.mjs"], repoRoot);
  run("node", ["scripts/bundle-runtime.mjs"], repoRoot);

  run("npm", ["run", unpacked ? "pack" : "dist"], path.join(repoRoot, "desktop"));
}

try {
  main();
} catch (err) {
  console.error(err.message);
  process.exit(1);
}
