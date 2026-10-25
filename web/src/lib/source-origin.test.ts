import { describe, expect, it } from "vitest";
import { originUrlOf, sourceOriginFromUrl, sourceUrlFromHtml } from "@/lib/source-origin";

function page(head: string): string {
  return `<!DOCTYPE html><html><head>${head}</head><body><p>x</p></body></html>`;
}

describe("originUrlOf", () => {
  it("prefers the address the file's own record carries", () => {
    // A PDF or Office file records it beside the bytes rather than in them.
    expect(
      originUrlOf({ sourceUrl: "https://a.example/paper.pdf", content: null }),
    ).toBe("https://a.example/paper.pdf");
  });

  it("falls back to the one in a captured page's head", () => {
    const html = page('<meta name="noteagent:source_url" content="https://a.example/p/1">');
    expect(originUrlOf({ content: html })).toBe("https://a.example/p/1");
  });

  it("takes the head when the record has nothing usable", () => {
    const html = page('<meta name="noteagent:source_url" content="https://a.example/p/1">');
    expect(originUrlOf({ sourceUrl: "   ", content: html })).toBe("https://a.example/p/1");
    expect(originUrlOf({ sourceUrl: "not a url", content: html })).toBe("https://a.example/p/1");
  });

  it("says nothing when neither has an address", () => {
    expect(originUrlOf({})).toBeNull();
    expect(originUrlOf({ sourceUrl: null, content: null })).toBeNull();
    expect(originUrlOf({ content: page('<meta charset="utf-8">') })).toBeNull();
  });
});

describe("sourceOriginFromUrl", () => {
  it("names the page and the site it is on", () => {
    expect(sourceOriginFromUrl("https://a.example/p/1")).toEqual({
      url: "https://a.example/p/1",
      domain: "a.example",
    });
  });

  it("trims what it was handed", () => {
    expect(sourceOriginFromUrl("  https://a.example/p/1  ")?.url).toBe(
      "https://a.example/p/1",
    );
  });

  it("says nothing for anything that is not a web address", () => {
    // A stored path and a local identity are not pages, and offering to open one
    // would be offering what cannot be opened.
    expect(sourceOriginFromUrl("raw/web/a.html")).toBeNull();
    expect(sourceOriginFromUrl("local:pdf:abc123")).toBeNull();
    expect(sourceOriginFromUrl("")).toBeNull();
    expect(sourceOriginFromUrl("javascript:alert(1)")).toBeNull();
  });
});

describe("sourceUrlFromHtml", () => {
  it("reads the address the capture recorded", () => {
    const html = page(
      '<meta charset="utf-8"><meta name="noteagent:source_url" content="https://a.example/p/1">',
    );
    expect(sourceUrlFromHtml(html)).toBe("https://a.example/p/1");
  });

  it("rebuilds an address the writer escaped", () => {
    const html = page(
      '<meta name="noteagent:source_url" content="https://a.example/s?q=1&amp;r=2&amp;x=%20">',
    );
    expect(sourceUrlFromHtml(html)).toBe("https://a.example/s?q=1&r=2&x=%20");
  });

  it("takes the attribute in either case and either kind of quote", () => {
    const html = page(
      "<META NAME='noteagent:source_url' CONTENT='https://a.example/x'>",
    );
    expect(sourceUrlFromHtml(html)).toBe("https://a.example/x");
  });

  it("ignores a page that records no address", () => {
    expect(sourceUrlFromHtml(page('<meta name="noteagent:fidelity" content="ok">'))).toBeNull();
    expect(sourceUrlFromHtml("<html><body>no head</body></html>")).toBeNull();
    expect(sourceUrlFromHtml("")).toBeNull();
    expect(sourceUrlFromHtml(null)).toBeNull();
  });

  it("refuses anything that is not a web address", () => {
    const html = page(
      '<meta name="noteagent:source_url" content="javascript:alert(1)">',
    );
    expect(sourceUrlFromHtml(html)).toBeNull();
  });

  it("does not look past the head", () => {
    const html = `<!DOCTYPE html><html><head><meta charset="utf-8"></head><body><meta name="noteagent:source_url" content="https://a.example/late"></body></html>`;
    expect(sourceUrlFromHtml(html)).toBeNull();
  });
});
