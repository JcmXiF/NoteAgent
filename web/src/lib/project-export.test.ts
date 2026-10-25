// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { downloadProjectArchive } from "@/lib/project-export";

/**
 * Exporting is one link to the core, and the details that matter are that it points
 * at this project and that it does not leave an element behind after the click.
 */

describe("downloading a project archive", () => {
  let clicked: string[];

  beforeEach(() => {
    clicked = [];
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      clicked.push(this.getAttribute("href") ?? "");
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    document.body.replaceChildren();
  });

  it("points the browser at this project's archive", () => {
    downloadProjectArchive("my wiki");
    expect(clicked).toEqual(["/api/projects/my%20wiki/export"]);
  });

  it("leaves nothing behind in the page", () => {
    downloadProjectArchive("p");
    expect(document.querySelector("a")).toBeNull();
  });
});
