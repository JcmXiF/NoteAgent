/**
 * Checks what the interface says against the project's wording rules.
 *
 * Three things the rules forbid, each of which reads as a defect to the person
 * using the app:
 *  - a path or a file name in text the reader sees ("under `projects/`" tells them
 *    about the machinery rather than about what happened);
 *  - the glossary's internal words in Chinese copy ("本轮" where the reader's word
 *    is "本次");
 *  - phrasing that reads like a note left for the developer rather than a sentence
 *    written for the reader ("可不填——用一两个词说一下目的");
 *  - Chinese written straight into a component, which then cannot be translated.
 *
 * Run with `npm run check:terms`; also part of `npm run build`.
 */
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const srcDir = join(here, "..", "src");
const localesDir = join(srcDir, "i18n", "locales");

/** The app's own plumbing, which is not the reader's world. */
const PATH_IN_COPY = [
  /\bwiki\//,
  /\braw\//,
  /\bprojects\//,
  /\bclips\.json/,
  /\bchats?\//,
  /\.md\b/,
  /\.json\b/,
  /\bsources\//,
  // A command belongs in the install notes, not on a screen.
  /noteagent\s+[a-z]/i,
];

/**
 * The glossary's Chinese ❌ entries.
 *
 * "块" is only jargon when it means a passage: "模块" is an ordinary word, so the
 * check looks at what precedes it.
 */
const JARGON = [
  /人闸/,
  /落盘/,
  /写盘/,
  /集合/,
  /本轮/,
  /派生/,
  /定位器/,
  /幽灵节点/,
  /摘要页/,
  /叶子/,
  /收口/,
  /未开工/,
  /后置/,
  /覆盖桶/,
  /主题闸/,
  /候选列表/,
  /(?<![模])块/,
];

/**
 * Wording that reads as a working note rather than as the interface.
 *
 * The dash-aside explains the field instead of naming what goes in it; the rest
 * tell the reader what to do with the widget. Both are the developer talking.
 */
const PATCH_NOTE = [/——/, /可不填/, /填\s*0\s*表示/, /这里还是/, /点一下/, /看看|试试/];

/** `{{name}}` is a placeholder: its name is never shown to anyone. */
const PLACEHOLDER = /\{\{[^}]*\}\}/g;

/** Chinese that is not interface wording. */
const ALLOWED_IN_COMPONENT = [
  // The languages are named in their own script.
  "i18n/index.ts",
  // Matching text that came from a page, or a marker the core writes.
  "lib/clips.ts",
];

function walk(dir, suffix) {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) return walk(path, suffix);
    return entry.name.endsWith(suffix) ? [path] : [];
  });
}

function catalogEntries(file) {
  const raw = JSON.parse(readFileSync(file, "utf8"));
  const entries = [];
  const walkValue = (value, prefix) => {
    if (value === null || typeof value !== "object" || Array.isArray(value)) {
      if (typeof value === "string") entries.push([prefix, value]);
      return;
    }
    for (const [key, child] of Object.entries(value)) {
      walkValue(child, prefix ? `${prefix}.${key}` : key);
    }
  };
  walkValue(raw, "");
  return entries;
}

let failed = false;
const fail = (message) => {
  failed = true;
  console.error(message);
};

const catalogs = readdirSync(localesDir).filter((name) => name.endsWith(".json"));

for (const name of catalogs) {
  for (const [key, text] of catalogEntries(join(localesDir, name))) {
    // The language toggle names a language the way that language writes it.
    if (key.startsWith("language")) continue;

    const shown = text.replace(PLACEHOLDER, "");
    for (const pattern of PATH_IN_COPY) {
      if (pattern.test(shown)) {
        fail(`${name}  ${key}: text shown to the reader carries a path or file name\n    ${text}`);
        break;
      }
    }
    if (name === "zh.json") {
      for (const pattern of JARGON) {
        const match = shown.match(pattern);
        if (match) {
          fail(`${name}  ${key}: "${match[0]}" is an internal word\n    ${text}`);
          break;
        }
      }
      for (const pattern of PATCH_NOTE) {
        const match = shown.match(pattern);
        if (match) {
          fail(
            `${name}  ${key}: "${match[0]}" reads as a note to the developer\n    ${text}`,
          );
          break;
        }
      }
    }
  }
}

const chinese = /[\u4e00-\u9fff]/;
for (const file of walk(srcDir, ".ts").concat(walk(srcDir, ".tsx"))) {
  const relativePath = relative(srcDir, file).replace(/\\/g, "/");
  if (relativePath.includes("i18n/locales")) continue;
  if (relativePath.includes(".test.")) continue;
  if (ALLOWED_IN_COMPONENT.some((allowed) => relativePath === allowed)) continue;

  readFileSync(file, "utf8")
    .split("\n")
    .forEach((line, index) => {
      const trimmed = line.trim();
      if (!chinese.test(trimmed)) return;
      if (trimmed.startsWith("//") || trimmed.startsWith("*") || trimmed.startsWith("/*")) return;
      fail(`${relativePath}:${index + 1}: Chinese written into the code\n    ${trimmed}`);
    });
}

if (failed) {
  console.error("\nSee the Contributing section of README.md for the wording rules.");
  process.exit(1);
}

console.log(
  `terms OK: ${catalogs.length} catalogs, ${PATH_IN_COPY.length} path patterns, ` +
    `${JARGON.length} jargon patterns, ${PATCH_NOTE.length} patch-note patterns.`,
);
