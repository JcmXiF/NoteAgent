import { beforeEach, describe, expect, it } from "vitest";
import type { ResourceCard } from "@/lib/api/types";
import { useAcquireStore } from "@/stores/acquire-store";

function card(url: string, title = url): ResourceCard {
  return {
    title,
    url,
    site: "example.com",
    siteName: "example.com",
    favicon: "",
    snippet: "",
    via: "",
    why: "",
  };
}

beforeEach(() => {
  useAcquireStore.setState({ results: [] });
});

describe("handing a result to the pile", () => {
  it("puts it in front, where the reader will look for it", () => {
    useAcquireStore.getState().setResults([card("https://a.example/1")]);
    useAcquireStore.getState().addResult(card("https://b.example/2"));
    expect(useAcquireStore.getState().results.map((item) => item.url)).toEqual([
      "https://b.example/2",
      "https://a.example/1",
    ]);
  });

  it("replaces a row for the same address rather than listing it twice", () => {
    useAcquireStore.getState().setResults([card("https://a.example/1", "旧标题")]);
    useAcquireStore.getState().addResult(card("https://a.example/1", "新标题"));
    const results = useAcquireStore.getState().results;
    expect(results).toHaveLength(1);
    expect(results[0]?.title).toBe("新标题");
  });

  it("does not read anything", () => {
    // Being handed a result is not fetching it: nothing lands in the list of
    // sources, which is the point of the tick list.
    useAcquireStore.getState().addResult(card("https://a.example/1"));
    expect(useAcquireStore.getState().entries).toEqual([]);
  });
});
