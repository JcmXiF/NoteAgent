/**
 * Checks the translation catalogs against each other, and against the keys the
 * code actually uses.
 *
 * Two failures this catches:
 *  - a catalog that has drifted, which would leave part of the interface in the
 *    fallback language;
 *  - a key referenced in code that no catalog defines, which renders the raw key
 *    on screen (e.g. "captureHint.unreadable").
 *
 * Run with `npm run check:i18n`; also part of `npm run build`.
 */
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const srcDir = join(here, "..", "src");
const localesDir = join(srcDir, "i18n", "locales");

/** Flattens `{ a: { b: "x" } }` into `["a.b"]` so key sets can be compared. */
function flattenKeys(value, prefix = "") {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    return [prefix];
  }
  return Object.entries(value).flatMap(([key, child]) =>
    flattenKeys(child, prefix ? `${prefix}.${key}` : key),
  );
}

function walk(dir) {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) return walk(path);
    return /\.(ts|tsx)$/.test(entry.name) ? [path] : [];
  });
}

/**
 * Keys passed to `t(...)` or `message(...)` as a plain string literal.
 * Template literals are skipped: their value is only known at runtime.
 */
function referencedKeys() {
  const pattern = /\b(?:t|message|i18n\.t)\(\s*"([A-Za-z][\w.]*)"/g;
  const found = new Map();
  let dynamic = 0;

  for (const file of walk(srcDir)) {
    if (file.startsWith(localesDir)) continue;
    const source = readFileSync(file, "utf8");
    for (const match of source.matchAll(pattern)) {
      const key = match[1];
      if (!found.has(key)) found.set(key, []);
      found.get(key).push(relative(srcDir, file));
    }
    dynamic += (source.match(/\b(?:t|i18n\.t)\(\s*`/g) ?? []).length;
  }
  return { found, dynamic };
}

const catalogs = readdirSync(localesDir)
  .filter((name) => name.endsWith(".json"))
  .map((name) => ({
    language: name.replace(/\.json$/, ""),
    keys: flattenKeys(JSON.parse(readFileSync(join(localesDir, name), "utf8"))),
  }));

if (catalogs.length < 2) {
  console.error("Expected at least two catalogs in src/i18n/locales.");
  process.exit(1);
}

let failed = false;
const [reference, ...others] = catalogs;
const referenceKeys = new Set(reference.keys);

for (const catalog of others) {
  const keys = new Set(catalog.keys);
  const missing = [...referenceKeys].filter((key) => !keys.has(key)).sort();
  const extra = [...keys].filter((key) => !referenceKeys.has(key)).sort();
  if (missing.length === 0 && extra.length === 0) continue;

  failed = true;
  console.error(`\n${catalog.language}.json does not match ${reference.language}.json:`);
  for (const key of missing) console.error(`  missing: ${key}`);
  for (const key of extra) console.error(`  unknown: ${key}`);
}

const { found, dynamic } = referencedKeys();
const undefinedKeys = [...found.entries()]
  .filter(([key]) => !referenceKeys.has(key))
  .sort(([a], [b]) => a.localeCompare(b));

if (undefinedKeys.length > 0) {
  failed = true;
  console.error("\nKeys used in code but not defined in the catalogs:");
  for (const [key, files] of undefinedKeys) {
    console.error(`  ${key}  (${[...new Set(files)].join(", ")})`);
  }
}

/**
 * Placeholders must match across languages, otherwise a value silently stays
 * literal in one of them, e.g. "{{count}} passages" shown in English and
 * "{{count}} 段" left as-is after a rename.
 */
function placeholders(text) {
  if (typeof text !== "string") return [];
  return [...text.matchAll(/\{\{\s*([\w.]+)\s*\}\}/g)].map((match) => match[1]).sort();
}

function entriesOf(value, prefix = "") {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    return [[prefix, value]];
  }
  return Object.entries(value).flatMap(([key, child]) =>
    entriesOf(child, prefix ? `${prefix}.${key}` : key),
  );
}

const byKey = new Map(catalogs.map((catalog) => [catalog.language, new Map()]));
for (const catalog of catalogs) {
  const raw = JSON.parse(readFileSync(join(localesDir, `${catalog.language}.json`), "utf8"));
  for (const [key, value] of entriesOf(raw)) byKey.get(catalog.language).set(key, value);
}

for (const key of referenceKeys) {
  const expected = placeholders(byKey.get(reference.language).get(key)).join(",");
  for (const catalog of others) {
    if (!catalog.keys.includes(key)) continue;
    const actual = placeholders(byKey.get(catalog.language).get(key)).join(",");
    if (actual === expected) continue;
    failed = true;
    console.error(
      `\nPlaceholders differ for ${key}: ` +
        `${reference.language}=[${expected}] ${catalog.language}=[${actual}]`,
    );
  }
}

if (failed) {
  console.error(`\n${referenceKeys.size} keys defined, ${found.size} referenced.`);
  process.exit(1);
}

const skipped = dynamic > 0 ? `, ${dynamic} dynamic skipped` : "";
console.log(
  `i18n OK: ${referenceKeys.size} keys, ${catalogs.length} languages, ` +
    `${found.size} referenced${skipped}.`,
);
