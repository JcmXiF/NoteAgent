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
});
