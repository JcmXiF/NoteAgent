import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ApiError,
  ShownError,
  coreDetail,
  errorMessage,
  isSseAborted,
  postSse,
  projectPath,
} from "@/lib/api/client";
import i18n from "@/i18n";

/** A streamed response whose events are given as `event:data` lines. */
function sseResponse(blocks: string[]): Response {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const block of blocks) controller.enqueue(new TextEncoder().encode(block));
      controller.close();
    },
  });
  return new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } });
}

describe("postSse token events", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("hands over the text of a token", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        sseResponse([
          'event: token\ndata: {"text": "信息"}\n\n',
          'event: done\ndata: {"id": "c1"}\n\n',
        ]),
      ),
    );
    const pieces: string[] = [];
    const resets: number[] = [];
    await postSse("/x", {}, { onToken: (t) => pieces.push(t), onTokenReset: () => resets.push(1) });
    expect(pieces).toEqual(["信息"]);
    expect(resets).toEqual([]);
  });

  it("reads a token without text as a retraction of what was sent", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        sseResponse([
          'event: token\ndata: {"text": "先查一下"}\n\n',
          'event: token\ndata: {"text": "", "reset": true}\n\n',
          'event: token\ndata: {"text": "答案"}\n\n',
          'event: done\ndata: {"id": "c1"}\n\n',
        ]),
      ),
    );
    const seen: string[] = [];
    await postSse("/x", {}, {
      onToken: (t) => seen.push(t),
      onTokenReset: () => seen.push("<reset>"),
    });
    // The retraction comes between the two, and is not mistaken for text.
    expect(seen).toEqual(["先查一下", "<reset>", "答案"]);
  });
});

describe("postSse error events", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("carries the code the core named out of the stream", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        sseResponse([
          'event: error\ndata: {"detail": "no clip collections to map", "code": "plan.noSelection"}\n\n',
        ]),
      ),
    );
    let caught: unknown;
    try {
      await postSse("/x", {});
    } catch (err) {
      caught = err;
    }
    expect(caught).toBeInstanceOf(ApiError);
    expect(errorMessage(caught, "fallback")).toBe(i18n.t("error.code.plan.noSelection"));
    expect(coreDetail(caught)).toBe("no clip collections to map");
  });

  it("leaves a job's figures on the request, not in the sentence", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        sseResponse([
          'event: error\ndata: {"detail": "pending", "code": "search.unsettled", "params": {"count": "2"}}\n\n',
        ]),
      ),
    );
    let caught: unknown;
    try {
      await postSse("/x", {});
    } catch (err) {
      caught = err;
    }
    expect(errorMessage(caught, "fallback")).toBe(
      i18n.t("error.code.search.unsettled", { count: "2" }),
    );
  });
});

describe("projectPath", () => {
  it("builds a project-scoped path", () => {
    expect(projectPath("rl", "tree")).toBe("/projects/rl/tree");
  });

  it("omits the suffix when there is none", () => {
    expect(projectPath("rl")).toBe("/projects/rl");
  });

  it("escapes the project name", () => {
    expect(projectPath("a b/c", "clips")).toBe("/projects/a%20b%2Fc/clips");
  });
});

describe("errorMessage", () => {
  it("says a sentence the interface has already worded", () => {
    expect(errorMessage(new ShownError("这个链接没能获取到"), "fallback")).toBe(
      "这个链接没能获取到",
    );
  });

  it("says the situation the core named, in the reader's language", () => {
    // The core names the situation; the wording on screen is the interface's own.
    const shown = errorMessage(
      new ApiError("no clip collections to map", 400, "plan.noSelection"),
      "fallback",
    );
    expect(shown).toBe(i18n.t("error.code.plan.noSelection"));
    expect(shown).not.toContain("clip");
  });

  it("puts the figures the code carries into the sentence", () => {
    const shown = errorMessage(
      new ApiError("project already exists: reading list", 409, "project.exists", {
        name: "reading list",
      }),
      "fallback",
    );
    expect(shown).toContain("reading list");
    expect(shown).not.toContain("{{name}}");
  });

  it("keeps wording written for a log off the screen, and in the log", () => {
    const logged = vi.spyOn(console, "info").mockImplementation(() => {});
    try {
      // No code, and a code this build has no sentence for, are one situation:
      // the core's own wording is for whoever reads a log.
      expect(errorMessage(new Error("no clip collections to map"), "fallback")).toBe("fallback");
      expect(errorMessage(new ApiError("still no plan", 400, "plan.nonsense"), "fallback")).toBe(
        "fallback",
      );
      expect(logged).toHaveBeenCalled();
    } finally {
      logged.mockRestore();
    }
  });

  it("falls back for a string throw and for nothing at all", () => {
    // A thrown string is not a sentence the interface wrote, either.
    expect(errorMessage("plain string", "fallback")).toBe("fallback");
    expect(errorMessage(undefined, "fallback")).toBe("fallback");
    expect(errorMessage(new Error(""), "fallback")).toBe("fallback");
    expect(errorMessage(new Error("   "), "fallback")).toBe("fallback");
  });

  it("says the service is out of reach rather than repeating the browser", () => {
    // A core that is not running is the common case, and "Failed to fetch" reads
    // like a fault in the page rather than "nothing is listening".
    for (const wording of ["Failed to fetch", "Load failed", "NetworkError when attempting to fetch resource."]) {
      const shown = errorMessage(new TypeError(wording), "fallback");
      expect(shown).not.toContain("fetch");
      expect(shown.length).toBeGreaterThan(0);
    }
  });

  it("only reads the browser's own wording as an unreachable service", () => {
    // A failure the core reported is not the same as nothing listening, even when
    // the wording mentions the network.
    const shown = errorMessage(new Error("这个链接没能获取到：网络超时"), "fallback");
    expect(shown).toBe("fallback");
    expect(shown).not.toBe(i18n.t("error.offline"));
  });

  it("does not put a refusal's file paths on screen", () => {
    const shown = errorMessage(
      new ApiError("file not found: raw/web/a.html", 404, "file.missing"),
      "fallback",
    );
    expect(shown).not.toContain("raw");
    expect(shown).not.toContain("file not found");
    expect(shown.length).toBeGreaterThan(0);
  });

  it("reads a gateway's answer as the service being out of reach", () => {
    // The dev proxy says 502 when the core is not running: the reader's situation is
    // the same as an unreachable service, and "HTTP 502" is nobody's sentence.
    for (const status of [502, 503, 504]) {
      const shown = errorMessage(new ApiError("HTTP 502", status), "fallback");
      expect(shown).not.toContain("502");
      expect(shown.length).toBeGreaterThan(0);
    }
  });
});

describe("coreDetail", () => {
  it("is what the core said, for the job's log", () => {
    expect(coreDetail(new ApiError("no clip collections to map", 400, "plan.noSelection"))).toBe(
      "no clip collections to map",
    );
  });

  it("is empty once the interface has worded the failure itself", () => {
    expect(coreDetail(new ShownError("这个链接没能获取到"))).toBe("");
  });
});

describe("a request that never gets there", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("is reported as the service being out of reach", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("Failed to fetch");
      }),
    );
    const { fetchTree } = await import("@/lib/api/files");
    await expect(fetchTree("p")).rejects.toThrow();
    try {
      await fetchTree("p");
    } catch (err) {
      expect(errorMessage(err, "fallback")).not.toContain("fetch");
    }
  });

  it("leaves an abort to the caller that asked for it", async () => {
    const controller = new AbortController();
    controller.abort();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("Failed to fetch");
      }),
    );
    await expect(postSse("/x", {}, { signal: controller.signal })).rejects.toThrow();
    const { postSse: again } = await import("@/lib/api/client");
    try {
      await again("/x", {}, { signal: controller.signal });
    } catch (err) {
      expect(isSseAborted(err, controller.signal)).toBe(true);
    }
  });
});

describe("isSseAborted", () => {
  it("is true once the signal is aborted", () => {
    const controller = new AbortController();
    controller.abort();
    expect(isSseAborted(new Error("anything"), controller.signal)).toBe(true);
  });

  it("recognises an abort by name", () => {
    const err = new Error("stopped");
    err.name = "AbortError";
    expect(isSseAborted(err)).toBe(true);
  });

  it("is false for an ordinary failure", () => {
    expect(isSseAborted(new Error("the site refused the request"))).toBe(false);
  });
});
