// Run the checks the project asks for, from one command and one interpreter.
//
// The CI workflow is the authority; this script mirrors its commands locally, so
// a contributor does not have to rebuild the list, pick a Python interpreter, or
// remember which directory each command runs in.
//
// Usage:
//   node scripts/check.mjs
//   node scripts/check.mjs --python /path/to/python
//   NOTEAGENT_PYTHON=/path/to/python node scripts/check.mjs
//
// A Python interpreter is accepted only if it can import the core and the API
// extra. The error names the variable to set rather than failing later inside a
// test with "No module named noteagent".

import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const webDir = path.join(repoRoot, "web");
const desktopDir = path.join(repoRoot, "desktop");
const sourceDir = path.join(repoRoot, "src");

function argument(name) {
  const index = process.argv.indexOf(name);
  return index >= 0 ? process.argv[index + 1] : undefined;
}

function pythonIn(base) {
  if (!base) return undefined;
  return process.platform === "win32"
    ? path.join(base, "python.exe")
    : path.join(base, "bin", "python");
}

function candidates() {
  const explicit = argument("--python");
  if (explicit) return [explicit];
  return [
    process.env.NOTEAGENT_PYTHON,
    process.env.PYTHON,
    pythonIn(process.env.VIRTUAL_ENV),
    pythonIn(process.env.CONDA_PREFIX),
    "python",
    "python3",
  ].filter(Boolean);
}

/** A command run verbatim, with the shell only where Windows needs it for npm. */
function run(command, args, cwd, env = process.env) {
  const shell = process.platform === "win32" && path.basename(command) === "npm";
  // Windows runs npm through a .cmd shim, which CreateProcess cannot start
  // directly. The npm steps are fixed words, so one command line is safe and
  // avoids Node's deprecated `shell` + args form.
  const result = shell
    ? spawnSync([command, ...args].join(" "), {
        cwd,
        env,
        stdio: "inherit",
        shell: true,
      })
    : spawnSync(command, args, { cwd, env, stdio: "inherit" });
  if (result.error) {
    throw new Error(`${command}: ${result.error.message}`);
  }
  if (result.status !== 0) {
    throw new Error(`${command} ${args.join(" ")} exited with ${result.status}`);
  }
}

function pythonEnv() {
  const parts = [sourceDir, process.env.PYTHONPATH].filter(Boolean);
  return { ...process.env, PYTHONPATH: parts.join(path.delimiter) };
}

function usable(command) {
  const probe = spawnSync(
    command,
    ["-c", "import noteagent, fastapi, keyring"],
    {
      cwd: repoRoot,
      env: pythonEnv(),
      stdio: "ignore",
    },
  );
  return probe.status === 0;
}

function findPython() {
  for (const command of candidates()) {
    if (usable(command)) return command;
  }
  throw new Error(
    "no Python interpreter can import noteagent, fastapi and keyring.\n" +
      "Install the dev dependencies (`pip install -e \".[api]\"`) or set " +
      "NOTEAGENT_PYTHON to one that can.",
  );
}

function main() {
  if (process.argv.includes("--help") || process.argv.includes("-h")) {
    console.log("Usage: node scripts/check.mjs [--python PATH]");
    return;
  }

  const python = findPython();
  const pyenv = pythonEnv();
  const steps = [
    {
      name: "Python tests",
      command: python,
      args: ["-m", "unittest", "discover", "-s", "tests"],
      cwd: repoRoot,
      env: pyenv,
    },
    {
      name: "Behaviour evals",
      command: python,
      args: ["-m", "evals"],
      cwd: repoRoot,
      env: pyenv,
    },
    {
      name: "Markdown",
      command: python,
      args: ["tests/check-docs.py"],
      cwd: repoRoot,
      env: pyenv,
    },
    {
      name: "Frontend types",
      command: "npm",
      args: ["run", "typecheck"],
      cwd: webDir,
    },
    {
      name: "Frontend tests",
      command: "npm",
      args: ["test"],
      cwd: webDir,
    },
    {
      name: "Frontend lint",
      command: "npm",
      args: ["run", "lint"],
      cwd: webDir,
    },
    {
      name: "Frontend build",
      command: "npm",
      args: ["run", "build"],
      cwd: webDir,
    },
    {
      name: "Electron shell",
      command: "npm",
      args: ["test"],
      cwd: desktopDir,
    },
  ];

  const started = Date.now();
  for (const step of steps) {
    console.log(`\n==> ${step.name}`);
    try {
      run(step.command, step.args, step.cwd, step.env);
    } catch (err) {
      console.error(`\n${step.name} failed: ${err.message}`);
      process.exit(1);
    }
  }
  const seconds = ((Date.now() - started) / 1000).toFixed(1);
  console.log(`\nAll checks passed (${steps.length} steps, ${seconds}s).`);
}

const isMain =
  process.argv[1] !== undefined &&
  path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);

if (isMain) {
  try {
    main();
  } catch (err) {
    console.error(err.message);
    process.exit(1);
  }
}
