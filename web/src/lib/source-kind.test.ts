import { describe, expect, it } from "vitest";
import {
  RAW_BUCKETS,
  isBindableWikiPage,
  isGeneratedWikiPage,
  isHtmlPath,
  isImagePath,
  isOfficePath,
  isPdfPath,
  isRawSourcePath,
  isRenderableOriginalPath,
  rawBucket,
} from "@/lib/source-kind";

describe("extension predicates", () => {
  it("recognises HTML", () => {
    expect(isHtmlPath("raw/web/a.html")).toBe(true);
    expect(isHtmlPath("raw/web/a.HTM")).toBe(true);
    expect(isHtmlPath("raw/web/a.htmlx")).toBe(false);
  });

  it("recognises PDF and Office files", () => {
    expect(isPdfPath("raw/pdf/a.pdf")).toBe(true);
    expect(isOfficePath("raw/office/a.docx")).toBe(true);
    expect(isOfficePath("raw/office/a.pptx")).toBe(true);
    expect(isOfficePath("raw/office/a.doc")).toBe(false);
  });

  it("recognises raster and vector images", () => {
    for (const name of ["a.png", "a.jpg", "a.JPEG", "a.gif", "a.webp", "a.svg", "a.avif"]) {
      expect(isImagePath(`raw/media/${name}`)).toBe(true);
    }
    expect(isImagePath("raw/media/a.bmp")).toBe(false);
  });

  it("normalises Windows separators", () => {
    expect(isPdfPath("raw\\sources\\pdf\\a.pdf")).toBe(true);
    expect(isRawSourcePath("raw\\sources\\web\\a.html")).toBe(true);
  });

  it("ignores a leading dot or a missing extension", () => {
    expect(isHtmlPath("README")).toBe(false);
    expect(isPdfPath(".pdf")).toBe(false);
  });
});

describe("isRenderableOriginalPath", () => {
  it("accepts everything the document view can show as an original", () => {
    expect(isRenderableOriginalPath("raw/web/a.html")).toBe(true);
    expect(isRenderableOriginalPath("raw/pdf/a.pdf")).toBe(true);
    expect(isRenderableOriginalPath("raw/office/a.pptx")).toBe(true);
    expect(isRenderableOriginalPath("raw/media/a.png")).toBe(true);
  });

  it("rejects text pages and markdown sources", () => {
    expect(isRenderableOriginalPath("wiki/concepts/a.md")).toBe(false);
    expect(isRenderableOriginalPath("raw/a.md")).toBe(false);
  });
});

describe("isRawSourcePath", () => {
  it("is scoped to the raw folder", () => {
    expect(isRawSourcePath("raw/web/a.html")).toBe(true);
    expect(isRawSourcePath("raw/other/a.html")).toBe(true);
    expect(isRawSourcePath("raw/sources/web/legacy.html")).toBe(true);
    expect(isRawSourcePath("wiki/a.md")).toBe(false);
    expect(isRawSourcePath("rawish/a.html")).toBe(false);
  });
});

describe("rawBucket", () => {
  it("files a source by its extension even when the folder disagrees", () => {
    expect(rawBucket("raw/web/a.html")).toBe("web");
    expect(rawBucket("raw/pdf/a.pdf")).toBe("pdf");
    expect(rawBucket("raw/office/a.docx")).toBe("office");
    expect(rawBucket("raw/media/a.png")).toBe("media");
    // A PDF that was filed under web/ still groups as a PDF.
    expect(rawBucket("raw/web/misfiled.pdf")).toBe("pdf");
  });

  it("falls back to the folder for an extension it does not know", () => {
    expect(rawBucket("raw/pdf/scan.xyz")).toBe("pdf");
    expect(rawBucket("raw/media/clip.bin")).toBe("media");
    // An older project's kind folder is still the kind folder.
    expect(rawBucket("raw/sources/pdf/scan.xyz")).toBe("pdf");
  });

  it("reports other for anything outside the buckets", () => {
    expect(rawBucket("raw/a.md")).toBe("other");
    expect(rawBucket("raw/unknown/a.bin")).toBe("other");
    expect(rawBucket("wiki/concepts/a.md")).toBe("other");
    expect(rawBucket("purpose.md")).toBe("other");
  });

  it("keeps the bucket list in sync with the buckets it returns", () => {
    expect(new Set(RAW_BUCKETS).size).toBe(RAW_BUCKETS.length);
    for (const bucket of RAW_BUCKETS) {
      expect(typeof bucket).toBe("string");
    }
  });
});

describe("wiki page predicates", () => {
  it("marks the pages the program maintains", () => {
    expect(isGeneratedWikiPage("wiki/index.md")).toBe(true);
    expect(isGeneratedWikiPage("wiki/overview.md")).toBe(true);
    expect(isGeneratedWikiPage("wiki/log.md")).toBe(true);
    expect(isGeneratedWikiPage(null)).toBe(false);
    expect(isGeneratedWikiPage(undefined)).toBe(false);
  });

  it("treats those file names as generated wherever they appear", () => {
    // Matching on the file name alone is deliberate: it is the conservative
    // direction, keeping the program's own pages out of citation targets.
    expect(isGeneratedWikiPage("wiki/concepts/index.md")).toBe(true);
    expect(isGeneratedWikiPage("wiki/sources/log.md")).toBe(true);
  });

  it("allows citations on content pages only", () => {
    expect(isBindableWikiPage("wiki/concepts/kl.md")).toBe(true);
    expect(isBindableWikiPage("wiki/index.md")).toBe(false);
    expect(isBindableWikiPage("wiki/overview.md")).toBe(false);
    expect(isBindableWikiPage("wiki/log.md")).toBe(false);
    // A source summary describes a whole file rather than citing passages.
    expect(isBindableWikiPage("wiki/sources/web-a.md")).toBe(false);
    expect(isBindableWikiPage("raw/web/a.html")).toBe(false);
    expect(isBindableWikiPage("wiki/concepts/notes.txt")).toBe(false);
    expect(isBindableWikiPage(null)).toBe(false);
  });
});
