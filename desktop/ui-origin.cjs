"use strict";

/**
 * Whether a URL belongs to the app's own interface.
 *
 * Compared by origin rather than by prefix. A prefix test would also accept a
 * host that merely begins with the same text — `127.0.0.1:5174.example.com` —
 * which is not the app.
 */
function makeIsUiOrigin(candidates) {
  let origins;
  try {
    origins = candidates.map((base) => new URL(base).origin);
  } catch (err) {
    throw new Error(`one of the UI candidates is not a URL: ${err.message}`);
  }

  return function isUiOrigin(raw) {
    let url;
    try {
      url = new URL(String(raw || ""));
    } catch {
      return false;
    }
    // The placeholder page the shell shows while waiting for the server.
    if (url.protocol === "data:") return true;
    return origins.includes(url.origin);
  };
}

module.exports = { makeIsUiOrigin };
