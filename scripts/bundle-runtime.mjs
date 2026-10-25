// Build the Python runtime that travels with the app.
//
// The app must run on a machine with no Python, no conda, and no Node, while the
// core it runs is the same `src/` this repository works on. So the runtime is the
// official embeddable CPython for Windows with the dependencies unpacked beside
// it, and `src/` is put on its import path by the `._pth` file that ships with
// the embeddable build.
//
// That `_pth` file is the whole trick. When it is present, Python runs isolated:
// PYTHONPATH and PYTHONHOME are ignored, `site` is not imported, and `sys.path`
// is exactly what the file says. Setting PYTHONPATH here would fail silently.
//
// Wheels are downloaded for cp312-win_amd64 specifically. Nothing from the build
// machine's environment is copied: if a dependency has no wheel, this fails
// loudly instead of shipping a binary built somewhere else.
//
// Usage: node scripts/bundle-runtime.mjs
// Env:   NOTEAGENT_PYTHON  interpreter used to download and unpack (default: python)

import { spawnSync } from "node:child_process";
import { cpSync, existsSync, mkdirSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync } from "node:fs";
import { createWriteStream } from "node:fs";
import path from "node:path";
import { Readable } from "node:stream";
import { pipeline } from "node:stream/promises";
import { fileURLToPath } from "node:url";

// The last 3.12 release python.org publishes Windows binaries for; later 3.12
// releases are source-only. The ABI is cp312 either way.
const PYTHON_VERSION = "3.12.10";
const PYTHON_URL = `https://www.python.org/ftp/python/${PYTHON_VERSION}/python-${PYTHON_VERSION}-embed-amd64.zip`;

// Kept in step with `pyproject.toml`: its `dependencies` plus the `api` extra,
// with one deliberate difference. `uvicorn`, not `uvicorn[standard]`: the extras
// bring watchfiles, websockets and httptools, which the packaged app does not
// use, and each one is another binary that can fail to load on a clean machine.
const REQUIREMENTS = [
  "httpx>=0.27",
  "trafilatura>=2.0",
  "lxml>=5.0",
  "charset-normalizer>=3.3",
  "PyYAML>=6.0",
  "pypdfium2>=4.30",
  "python-docx>=1.1",
  "python-pptx>=1.0",
  "fastapi>=0.115",
  "pydantic>=2.0",
  "starlette>=0.37",
  "uvicorn>=0.30",
];

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const buildRoot = path.join(repoRoot, "build", "runtime");
const coreRoot = path.join(buildRoot, "core");
const pythonRoot = path.join(coreRoot, "python");
const sitePackages = path.join(pythonRoot, "Lib", "site-packages");
const cacheRoot = path.join(repoRoot, "build", "cache");
const zipPath = path.join(cacheRoot, `python-${PYTHON_VERSION}-embed-amd64.zip`);
const wheelRoot = path.join(cacheRoot, "wheels");
const sourceRoot = path.join(repoRoot, "src");
const webEntry = path.join(sourceRoot, "noteagent", "web", "index.html");

const python = process.env.NOTEAGENT_PYTHON || "python";

function run(command, args, options = {}) {
  const result = spawnSync(command, args, { stdio: "inherit", cwd: repoRoot, ...options });
  if (result.status !== 0) {
    throw new Error(`${command} ${args.join(" ")} exited with ${result.status}`);
  }
}

async function download(url, target) {
  if (existsSync(target) && statSync(target).size > 1_000_000) {
    console.log(`using cached ${path.basename(target)}`);
    return;
  }
  mkdirSync(path.dirname(target), { recursive: true });
  console.log(`downloading ${url}`);
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${url} answered ${response.status}`);
  await pipeline(Readable.fromWeb(response.body), createWriteStream(target));
}

function extractZip(zip, destination) {
  const script =
    "import sys, zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])";
  run(python, ["-c", script, zip, destination]);
}

function writePth() {
  // Relative entries are resolved against the folder holding python.exe.
  const lines = [
    "python312.zip",
    ".",
    "Lib\\site-packages",
    "..\\src",
    "import site",
    "",
  ];
  writeFileSync(path.join(pythonRoot, "python312._pth"), lines.join("\r\n"), "utf8");
}

function copySources() {
  cpSync(sourceRoot, path.join(coreRoot, "src"), {
    recursive: true,
    filter: (source) => {
      const name = path.basename(source);
      if (name === "__pycache__") return false;
      if (name.endsWith(".pyc")) return false;
      // Build metadata names the machine that built a wheel; it says nothing
      // about the runtime this copy is.
      if (name.endsWith(".egg-info") || name.endsWith(".dist-info")) return false;
      return true;
    },
  });
}

function report() {
  let bytes = 0;
  let files = 0;
  const walk = (dir) => {
    for (const item of readdirSync(dir, { withFileTypes: true })) {
      const child = path.join(dir, item.name);
      if (item.isDirectory()) {
        walk(child);
      } else {
        files += 1;
        bytes += statSync(child).size;
      }
    }
  };
  walk(coreRoot);
  console.log(`runtime: ${files} files, ${(bytes / 1024 / 1024).toFixed(1)} MB at ${coreRoot}`);
}

async function main() {
  if (!existsSync(webEntry)) {
    console.error(
      "The interface is not built into src/noteagent/web. Run: npm run build (in web/), then node scripts/build-web.mjs",
    );
    process.exit(1);
  }

  rmSync(coreRoot, { recursive: true, force: true });
  mkdirSync(pythonRoot, { recursive: true });
  mkdirSync(sitePackages, { recursive: true });

  await download(PYTHON_URL, zipPath);
  extractZip(zipPath, pythonRoot);
  writePth();

  const requirements = path.join(buildRoot, "requirements.txt");
  writeFileSync(requirements, REQUIREMENTS.join("\n") + "\n", "utf8");

  mkdirSync(wheelRoot, { recursive: true });
  run(python, [
    "-m",
    "pip",
    "download",
    "--only-binary=:all:",
    "--platform",
    "win_amd64",
    "--python-version",
    "312",
    "--implementation",
    "cp",
    "--abi",
    "cp312",
    "--dest",
    wheelRoot,
    "-r",
    requirements,
  ]);

  // Installed from the downloaded wheels alone: nothing is fetched here, and
  // nothing already present in the build environment can be picked up.
  run(python, [
    "-m",
    "pip",
    "install",
    "--no-index",
    "--find-links",
    wheelRoot,
    "--only-binary=:all:",
    "--platform",
    "win_amd64",
    "--python-version",
    "312",
    "--implementation",
    "cp",
    "--abi",
    "cp312",
    "--target",
    sitePackages,
    "--no-compile",
    "-r",
    requirements,
  ]);

  const wheels = readdirSync(wheelRoot)
    .filter((name) => name.endsWith(".whl"))
    .sort();
  writeFileSync(path.join(buildRoot, "wheels.txt"), wheels.join("\n") + "\n", "utf8");

  copySources();

  // Every package the core imports directly. `pptx` pulls Pillow in on import,
  // so a separate `PIL` line would only repeat that.
  const probe = [
    "import fastapi, uvicorn, httpx, trafilatura, pypdfium2, lxml",
    "import docx, pptx, yaml, charset_normalizer, pydantic, starlette",
    "print('imports ok')",
  ].join("; ");
  run(path.join(pythonRoot, "python.exe"), ["-c", probe]);
  run(path.join(pythonRoot, "python.exe"), ["-c", "import noteagent, noteagent.api; print('source on path')"]);

  report();
}

main().catch((err) => {
  console.error(err.message);
  process.exit(1);
});
