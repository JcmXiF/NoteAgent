// @vitest-environment happy-dom
import { EditorView } from "codemirror";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NoteEditor } from "@/components/notes/NoteEditor";

/**
 * The writing surface, as the screen around it uses it.
 *
 * What the editor has to do is narrow and easy to get wrong: hold the text it is
 * given, hand back what is typed, take text that arrives from elsewhere without
 * reporting it back as typing, and say when the writer has left it — because
 * leaving is what gets the text kept. And it has to stay a plain text editor: the
 * markdown marks are what is stored, so they are on screen, not hidden behind a
 * rendered version.
 */

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

/** The element the editor writes into, failing when there is none. */
function content(): HTMLElement {
  const node = host.querySelector<HTMLElement>(".cm-content");
  if (!node) throw new Error("the editor did not mount");
  return node;
}

/** The editor itself, reached the way CodeMirror offers: from its own DOM. */
function editor(): EditorView {
  const view = EditorView.findFromDOM(content());
  if (!view) throw new Error("no editor behind the content");
  return view;
}

function mount(props: Parameters<typeof NoteEditor>[0]) {
  act(() => {
    root.render(<NoteEditor {...props} />);
  });
}

describe("the surface a remark is written on", () => {
  it("holds what it is given, and hands back what is typed into it", () => {
    const onChange = vi.fn();
    mount({ value: "第一句。", onChange, label: "备注内容" });

    expect(content().textContent).toContain("第一句。");

    act(() => {
      const view = editor();
      view.dispatch({ changes: { from: view.state.doc.length, insert: "又一句。" } });
    });

    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenCalledWith("第一句。又一句。");
  });

  it("takes text that arrives from elsewhere, and does not report it as typing", () => {
    const onChange = vi.fn();
    mount({ value: "甲", onChange, label: "备注内容" });
    mount({ value: "换成另一条备注。", onChange, label: "备注内容" });

    expect(content().textContent).toContain("换成另一条备注。");
    expect(onChange).not.toHaveBeenCalled();
  });

  it("says when the writer leaves it, but not when they move inside it", () => {
    const onBlur = vi.fn();
    mount({ value: "", onChange: () => undefined, onBlur, label: "备注内容" });

    act(() => {
      content().dispatchEvent(
        new FocusEvent("focusout", { bubbles: true, relatedTarget: content() }),
      );
    });
    expect(onBlur).not.toHaveBeenCalled();

    act(() => {
      content().dispatchEvent(
        new FocusEvent("focusout", { bubbles: true, relatedTarget: document.body }),
      );
    });
    expect(onBlur).toHaveBeenCalledTimes(1);
  });

  it("keeps the marks visible, since what is stored is the text itself", () => {
    mount({ value: "**粗** 与 # 标题", onChange: () => undefined, label: "备注内容" });

    expect(content().textContent).toContain("**粗**");
    expect(content().textContent).toContain("# 标题");
  });
});
