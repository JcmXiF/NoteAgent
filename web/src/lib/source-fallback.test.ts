import { describe, expect, it } from "vitest";
import { fallbackFor } from "@/lib/source-fallback";

describe("fallbackFor", () => {
  it("asks for a page again, and offers the browser when it is not open there", () => {
    expect(fallbackFor({ address: "https://example.com/a" })).toEqual({
      refetchUrl: "https://example.com/a",
      browserUrl: "https://example.com/a",
      readdFile: false,
    });
  });

  it("leaves the browser out when the page is already in it", () => {
    // Taking the page from the browser keeps whatever the reader signed in for;
    // opening the address again would start that over.
    expect(fallbackFor({ address: "https://example.com/a", inBrowser: true })).toEqual({
      refetchUrl: "https://example.com/a",
      browserUrl: null,
      readdFile: false,
    });
  });

  it("has a file added again when the address is its own identity", () => {
    expect(fallbackFor({ address: "local:html:4a073a162c2b" })).toEqual({
      refetchUrl: null,
      browserUrl: null,
      readdFile: true,
    });
  });

  it("offers nothing for an address it cannot ask for", () => {
    // A source named by the path it was stored at says nothing about where it came
    // from, so there is no request to make and no page to open.
    for (const address of ["", "   ", "raw/web/a.html"]) {
      expect(fallbackFor({ address })).toEqual({
        refetchUrl: null,
        browserUrl: null,
        readdFile: false,
      });
    }
  });

  it("ignores surrounding whitespace", () => {
    expect(fallbackFor({ address: "  https://example.com/a  " }).refetchUrl).toBe(
      "https://example.com/a",
    );
  });
});
