"use strict";

// What the guest browser will and will not treat as a page, and what a page's
// answer says about whether it is worth keeping.
//
// Both are pure functions so they can be checked without Electron: the shell
// only supplies the Electron objects.

/** A status the request was refused with, rather than answered. */
function httpAccessReason(status) {
  const code = Number(status);
  if (code === 401) return "http_unauthorized";
  if (code === 403) return "http_forbidden";
  if (code === 429) return "http_rate_limited";
  return "";
}

/** An address the guest browser will not load, named by why. */
class GuestUrlError extends Error {
  constructor(code) {
    super(code);
    this.code = code;
  }
}

/**
 * The address a browser tab may load, or an error saying why not.
 *
 * Only http(s) is allowed: a guest tab is a page being read, not a way to open
 * local files or another application's scheme handler.
 */
function parseHttpUrl(raw) {
  const text = String(raw ?? "").trim();
  let parsed;
  try {
    parsed = new URL(text);
  } catch {
    throw new GuestUrlError("not-url");
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    throw new GuestUrlError("not-http");
  }
  return parsed.href;
}

function cookiePresence(cookies) {
  const count = Array.isArray(cookies) ? cookies.length : 0;
  return { hasCookies: count > 0, cookieCount: count };
}

function publicGuestAuth(tab) {
  const source = tab && typeof tab === "object" ? tab : {};
  const httpStatus =
    typeof source.httpStatus === "number" ? source.httpStatus : null;
  const cookieCount = Number(source.cookieCount) || 0;
  return {
    httpStatus,
    hasCookies: Boolean(source.hasCookies) && cookieCount > 0,
    cookieCount,
  };
}

function isMainDocumentNavigation(args) {
  const first = args[0];
  if (first && typeof first === "object" && "isMainFrame" in first) {
    return Boolean(first.isMainFrame) && first.isSameDocument !== true;
  }
  const isInPlace = args[2];
  const isMainFrame = args[3];
  if (typeof isMainFrame === "boolean") {
    return isMainFrame && isInPlace !== true;
  }
  return false;
}

module.exports = {
  GuestUrlError,
  cookiePresence,
  httpAccessReason,
  isMainDocumentNavigation,
  parseHttpUrl,
  publicGuestAuth,
};
