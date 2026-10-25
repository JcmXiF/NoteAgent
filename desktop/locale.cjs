"use strict";

// Which of the two catalogs a locale asks for.
//
// Only two languages exist. Anything that starts with "zh" is Chinese; everything
// else gets English, which is also what the interface falls back to.

function languageFromLocale(locale) {
  return String(locale || "").toLowerCase().startsWith("zh") ? "zh" : "en";
}

module.exports = { languageFromLocale };
