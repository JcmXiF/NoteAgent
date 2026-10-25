import { describe, expect, it } from "vitest";
import { draftOf, draftSave, newDraft, type Draft } from "@/lib/note-draft";
import type { Note } from "@/lib/api/notes";

/**
 * What writing a remark does when it is committed.
 *
 * There is no save button, so the shape of the remark at the moment of leaving is
 * what decides: nothing written and never written leaves nothing, nothing written
 * and already written removes it, anything else is written. These check that, and
 * that an emptied remark which is then typed into again is an edit rather than a
 * removal.
 */

const NOTE: Note = {
  id: "n1",
  page: "wiki/互信息.md",
  anchor: "定义",
  quote: "互信息衡量两个随机变量相互依赖的程度",
  created: "2026-10-11T10:00:00+08:00",
  updated: "2026-10-11T10:00:00+08:00",
  body: "原本写的话。",
};

function draft(over: Partial<Draft>): Draft {
  return { ...newDraft("wiki/互信息.md", { anchor: "", quote: "" }), ...over };
}

describe("writing a remark for the first time", () => {
  it("leaves nothing behind when nothing was written", () => {
    expect(draftSave(draft({ body: "   \n " }))).toEqual({ kind: "nothing" });
  });

  it("creates the remark once something is written", () => {
    expect(draftSave(draft({ body: "写下的第一句话。" }))).toEqual({
      kind: "create",
      fields: { body: "写下的第一句话。", anchor: "", quote: "" },
    });
  });

  it("creates it where the writer put it, with what was quoted", () => {
    expect(
      draftSave(draft({ body: "一句话。", anchor: "定义", quote: "互信息衡量" })),
    ).toEqual({
      kind: "create",
      fields: { body: "一句话。", anchor: "定义", quote: "互信息衡量" },
    });
  });
});

describe("a remark that is already written", () => {
  const existing = draftOf(NOTE);

  it("writes nothing when nothing changed", () => {
    expect(draftSave(existing)).toEqual({ kind: "nothing" });
  });

  it("writes the new text when it changed", () => {
    const save = draftSave({ ...existing, body: "改过的话。" });
    expect(save).toEqual({
      kind: "update",
      id: "n1",
      fields: { body: "改过的话。", anchor: "定义", quote: NOTE.quote },
    });
  });

  it("counts a change of place as a change", () => {
    expect(draftSave({ ...existing, anchor: "计算式" }).kind).toBe("update");
    expect(draftSave({ ...existing, quote: "别的一段话" }).kind).toBe("update");
  });

  it("removes it when it was emptied", () => {
    expect(draftSave({ ...existing, body: "" })).toEqual({ kind: "delete", id: "n1" });
    expect(draftSave({ ...existing, body: "\n\n" })).toEqual({
      kind: "delete",
      id: "n1",
    });
  });

  it("edits rather than removes it when the emptied remark is written into again", () => {
    // Selecting everything and typing over it is a normal way to rewrite a remark;
    // the empty moment in the middle of it is not a decision about the remark.
    const save = draftSave({ ...existing, body: "重写之后的话。" });
    expect(save.kind).toBe("update");
  });
});
