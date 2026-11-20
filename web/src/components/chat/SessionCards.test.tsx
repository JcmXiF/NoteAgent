// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import i18n from "@/i18n";
import { SessionCards } from "@/components/chat/SessionCards";
import type { ObservationPayload } from "@/lib/api/client";

let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => {
    root.unmount();
  });
  host.remove();
});

async function draw(observations: ObservationPayload[]) {
  await act(async () => {
    root.render(<SessionCards observations={observations} />);
  });
}

describe("the cards beside an answer", () => {
  it("draws a web search as a query and the cards it found", async () => {
    await draw([
      {
        tool: "web_search",
        title: "entropy",
        query: "entropy",
        hits: 2,
        rev: 3,
        cards: [
          {
            id: "card-1",
            title: "Entropy",
            url: "https://example.com/entropy",
            snippet: "熵衡量不确定性。",
            site: "example.com",
          },
          {
            id: "card-2",
            title: "Information",
            url: "https://example.com/information",
            snippet: "信息是不确定性的减少。",
            site: "example.com",
          },
        ],
      },
    ]);

    expect(host.textContent).toContain("entropy");
    expect(host.textContent).toContain(i18n.t("chat.looked.hits", { count: 2 }));
    expect(host.textContent).toContain("Entropy");
    expect(host.textContent).toContain("Information");
  });

  it("says when a search is still running", async () => {
    await draw([
      {
        tool: "web_search",
        title: "entropy",
        query: "entropy",
        status: "running",
        rev: 0,
      },
    ]);

    expect(host.textContent).toContain(i18n.t("chat.looked.searching"));
  });

  it("draws a picked card as a material with its block count", async () => {
    await draw([
      {
        tool: "pick_card",
        title: "Entropy",
        url: "https://example.com/entropy",
        fidelity: "ok",
        rev: 3,
        materials: [
          {
            id: "raw/web/entropy-a1.html",
            title: "Entropy",
            url: "https://example.com/entropy",
            kind: "html",
            fidelity: "ok",
            state: "staged",
            blockCount: 12,
            blocksOmitted: 10,
          },
        ],
      },
    ]);

    expect(host.textContent).toContain("Entropy");
    expect(host.textContent).toContain(i18n.t("chat.looked.blocks", { count: 12 }));
    expect(host.textContent).not.toContain("block-1");
  });

  it("draws a frame as the material it marks and how much it covers", async () => {
    await draw([
      {
        tool: "frame_blocks",
        title: "raw/web/entropy-a1.html",
        rev: 3,
        frames: [
          {
            id: "frame-1",
            docId: "raw/web/entropy-a1.html",
            source: "agent",
            blockCount: 2,
            blocks: [{ id: "b1" }, { id: "b2" }],
          },
        ],
      },
    ]);

    expect(host.textContent).toContain("raw/web/entropy-a1.html");
    expect(host.textContent).toContain(
      i18n.t("chat.looked.blocks", { count: 2 }),
    );
    // A card says how much the frame covers, not which block ids it names.
    expect(host.textContent).not.toContain("b1");
  });

  it("draws every frame an inspect returned", async () => {
    await draw([
      {
        tool: "inspect",
        title: "writing task frames",
        rev: 4,
        hits: 2,
        frames: [
          {
            id: "frame-1",
            docId: "raw/web/entropy-a1.html",
            source: "human",
            blockCount: 1,
            blocks: [{ id: "b1", preview: "熵衡量不确定性。" }],
          },
          {
            id: "frame-2",
            docId: "raw/web/information-a1.html",
            source: "agent",
            blockCount: 3,
            blocks: [],
          },
        ],
      },
    ]);

    expect(host.textContent).toContain("raw/web/entropy-a1.html");
    expect(host.textContent).toContain("raw/web/information-a1.html");
    expect(host.textContent).toContain(
      i18n.t("chat.looked.blocks", { count: 3 }),
    );
  });

  it("draws a write request as its page and sections, waiting for approval", async () => {
    await draw([
      {
        tool: "compose_page",
        title: "Entropy",
        status: "needs_approval",
        rev: 4,
        request: {
          frameId: "frame-1",
          rev: 4,
          at: "2026-11-10T09:05:00Z",
          spec: {
            slug: "entropy",
            title: "Entropy",
            action: "create",
            sections: [
              { title: "What it is", blocks: ["b1"] },
              { title: "Why it matters", blocks: ["b2", "b3"] },
            ],
          },
        },
      },
    ]);

    expect(host.textContent).toContain("Entropy");
    expect(host.textContent).toContain("What it is");
    expect(host.textContent).toContain("Why it matters");
    expect(host.textContent).toContain(i18n.t("chat.looked.needsApproval"));
    expect(host.textContent).toContain(
      i18n.t("chat.looked.blocks", { count: 2 }),
    );
  });

  it("says a compose_page was approved", async () => {
    await draw([
      {
        tool: "compose_page",
        title: "Entropy",
        status: "approved",
        rev: 5,
      },
    ]);

    expect(host.textContent).toContain("Entropy");
    expect(host.textContent).toContain(i18n.t("chat.looked.approved"));
    expect(host.textContent).not.toContain(i18n.t("chat.looked.needsApproval"));
  });

  it("says when a pick is still fetching", async () => {
    await draw([
      {
        tool: "pick_card",
        title: "card-1",
        status: "running",
        rev: 2,
      },
    ]);

    expect(host.textContent).toContain(i18n.t("chat.looked.picking"));
  });
});
