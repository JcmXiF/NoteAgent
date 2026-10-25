"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const {
  GuestUrlError,
  cookiePresence,
  httpAccessReason,
  isMainDocumentNavigation,
  parseHttpUrl,
  publicGuestAuth,
} = require("../guest-auth.cjs");

test("http access reasons follow status codes only", () => {
  assert.equal(httpAccessReason(401), "http_unauthorized");
  assert.equal(httpAccessReason(403), "http_forbidden");
  assert.equal(httpAccessReason(429), "http_rate_limited");
  assert.equal(httpAccessReason(404), "");
  assert.equal(httpAccessReason(200), "");
  assert.equal(httpAccessReason(null), "");
});

test("a guest tab loads http and https, with the address as given", () => {
  assert.equal(parseHttpUrl("https://example.com/a"), "https://example.com/a");
  assert.equal(parseHttpUrl("http://example.com"), "http://example.com/");
});

test("an address that is not a URL is refused by name", () => {
  assert.throws(
    () => parseHttpUrl("not a url"),
    (err) => err instanceof GuestUrlError && err.code === "not-url",
  );
  assert.throws(
    () => parseHttpUrl(""),
    (err) => err instanceof GuestUrlError && err.code === "not-url",
  );
  assert.throws(
    () => parseHttpUrl("https://"),
    (err) => err instanceof GuestUrlError && err.code === "not-url",
  );
});

test("only http(s) can be a guest page, whatever else is a URL", () => {
  for (const raw of ["file:///etc/passwd", "javascript:alert(1)", "mailto:a@b.c"]) {
    assert.throws(
      () => parseHttpUrl(raw),
      (err) => err instanceof GuestUrlError && err.code === "not-http",
      raw,
    );
  }
});

test("cookie presence never keeps values", () => {
  const summary = cookiePresence([
    { name: "sid", value: "secret-token", domain: "example.com" },
  ]);
  assert.deepEqual(summary, { hasCookies: true, cookieCount: 1 });
  assert.equal(JSON.stringify(summary).includes("secret-token"), false);
  assert.deepEqual(cookiePresence([]), { hasCookies: false, cookieCount: 0 });
});

test("public guest auth drops extra cookie fields", () => {
  const out = publicGuestAuth({
    httpStatus: 200,
    hasCookies: true,
    cookieCount: 2,
    cookies: [{ name: "sid", value: "secret-token" }],
  });
  assert.deepEqual(out, {
    httpStatus: 200,
    hasCookies: true,
    cookieCount: 2,
  });
  assert.equal("cookies" in out, false);
  assert.equal(JSON.stringify(out).includes("secret-token"), false);
});

test("main-document navigation reset uses protocol flags", () => {
  assert.equal(
    isMainDocumentNavigation([{ isMainFrame: true, isSameDocument: false }]),
    true,
  );
  assert.equal(
    isMainDocumentNavigation([{ isMainFrame: true, isSameDocument: true }]),
    false,
  );
  assert.equal(isMainDocumentNavigation([{}, "https://x", false, true]), true);
  assert.equal(isMainDocumentNavigation([{}, "https://x", true, true]), false);
  assert.equal(isMainDocumentNavigation([{}, "https://x", false, false]), false);
});
