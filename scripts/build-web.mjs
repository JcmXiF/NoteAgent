// Copy a built interface into the Python package.
//
// The core serves the interface from inside its own package, so a release has to
// carry the build. The copy is replaced wholesale: leftover files from an older
// build keep their old content hash in the name, and nothing asks for them again,
// so they would sit in the package forever.
//
// Run after `npm run build` in web/:  node scripts/build-web.mjs

import { cpSync, existsSync, mkdirSync, readdirSync, rmSync, statSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const source = path.join(repoRoot, "web", "dist");
const target = path.join(repoRoot, "src", "noteagent", "web");
const entry = path.join(source, "index.html");

function countFiles(dir) {
  let files = 0;
  let bytes = 0;
  for (const item of readdirSync(dir, { withFileTypes: true })) {
    const child = path.join(dir, item.name);
    if (item.isDirectory()) {
      const inner = countFiles(child);
      files += inner.files;
      bytes += inner.bytes;
    } else {
      files += 1;
      bytes += statSync(child).size;
    }
  }
  return { files, bytes };
}

if (!existsSync(entry)) {
  console.error(
    `No interface build at ${source}. Build it first: npm run build (in web/)`,
  );
  process.exit(1);
}

rmSync(target, { recursive: true, force: true });
mkdirSync(target, { recursive: true });
cpSync(source, target, { recursive: true });

const { files, bytes } = countFiles(target);
const megabytes = (bytes / 1024 / 1024).toFixed(2);
console.log(`Copied ${files} files (${megabytes} MB) to ${path.relative(repoRoot, target)}`);
