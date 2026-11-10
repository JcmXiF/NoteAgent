// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "@/i18n";
import { SessionView } from "@/components/chat/SessionView";
import { ApiError, type WaitPayload } from "@/lib/api/client";
import {
  fetchSession,
  postSessionCancel,
  postSessionQuery,
  postSessionWaitCancel,
} from "@/lib/api/sessions";
import type { SessionPayload } from "@/lib/api/types";

vi.mock("@/lib/api/sessions", () => ({
  fetchSession: vi.fn(),
  postSessionCancel: vi.fn(),
  postSessionQuery: vi.fn(),
  postSessionWaitCancel: vi.fn(),
}));

const payload: SessionPayload = {
  id: "s1",
  title: "Entropy",
  updatedAt: "2026-11-10T09:00:00Z",
  state: { busy: false },
  messages: [
    {
      id: "e1",
      role: "user",
      content: "什么是熵？",
      createdAt: "2026-11-10T09:00:00Z",
    },
    {
      id: "e2",
      role: "assistant",
      content: "我先查一下。",
      createdAt: "2026-11-10T09:00:01Z",
      isStep: true,
    },
    {
      id: "e3",
      role: "assistant",
      content: "熵衡量不确定性。",
      createdAt: "2026-11-10T09:00:02Z",
      isStep: false,
    },
  ],
  derived: {
    sections: {},
    summary: "",
    messages: [],
    diagnostics: [],
  },
};

const proposedWait = {
  waitId: "wait-1",
  job: "plan",
  interface: "source",
  status: "proposed",
  completion: "start_plan",
  proposedAt: "2026-11-10T09:00:00Z",
  intent: "plan entropy",
};

let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
  vi.mocked(fetchSession).mockReset();
  vi.mocked(postSessionCancel).mockReset();
  vi.mocked(postSessionQuery).mockReset();
  vi.mocked(postSessionWaitCancel).mockReset();
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

async function draw(
  sessionId = "s1",
  onOpenLink?: (target: string) => void,
  onOpenInterface?: (wait: WaitPayload, sessionId: string) => void,
  onSessionChanged?: (session: SessionPayload) => void,
) {
  await act(async () => {
    root.render(
      <SessionView
        sessionId={sessionId}
        slot="main"
        onOpenLink={onOpenLink}
        onOpenInterface={onOpenInterface}
        onSessionChanged={onSessionChanged}
      />,
    );
  });
  await act(async () => {});
}

async function ask(question: string) {
  const textarea = host.querySelector("textarea");
  expect(textarea).not.toBeNull();
  const setter = Object.getOwnPropertyDescriptor(
    HTMLTextAreaElement.prototype,
    "value",
  )?.set;
  await act(async () => {
    setter?.call(textarea, question);
    textarea?.dispatchEvent(new Event("input", { bubbles: true }));
  });
  const form = host.querySelector("form");
  expect(form).not.toBeNull();
  await act(async () => {
    form?.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
  });
  await act(async () => {});
}

describe("a session read from its log", () => {
  it("loads the session and draws its turns", async () => {
    vi.mocked(fetchSession).mockResolvedValue(payload);

    await draw();

    expect(fetchSession).toHaveBeenCalledWith("s1");
    expect(host.textContent).toContain("Entropy");
    expect(host.textContent).toContain("什么是熵？");
    expect(host.textContent).toContain("熵衡量不确定性。");
    expect(host.textContent).toContain(i18n.t("chat.step.tool"));
  });

  it("offers to keep the answer as a remark, but not its preamble", async () => {
    vi.mocked(fetchSession).mockResolvedValue(payload);

    await draw();

    const save = [...host.querySelectorAll("button")].filter((button) =>
      button.textContent?.includes(i18n.t("chat.remark.save")),
    );
    expect(save).toHaveLength(1);
  });

  it("shows a new answer as it is written, then the committed session", async () => {
    vi.mocked(fetchSession).mockResolvedValue(payload);
    vi.mocked(postSessionQuery).mockImplementation(
      async (_sessionId, _content, handlers) => {
        handlers?.onToken?.("熵是");
        handlers?.onToken?.("信息的平均量。");
        return {
          ...payload,
          messages: [
            ...payload.messages,
            {
              id: "e4",
              role: "user",
              content: "继续",
              createdAt: "2026-11-10T09:00:03Z",
            },
            {
              id: "e5",
              role: "assistant",
              content: "熵是信息的平均量。",
              createdAt: "2026-11-10T09:00:04Z",
            },
          ],
        };
      },
    );

    await draw();
    await ask("继续");

    expect(postSessionQuery).toHaveBeenCalledWith(
      "s1",
      "继续",
      expect.any(Object),
    );
    expect(host.textContent).toContain("继续");
    expect(host.textContent).toContain("熵是信息的平均量。");
  });

  it("draws tokens before the run finishes, takes back a preamble, and ignores a second submit", async () => {
    vi.mocked(fetchSession).mockResolvedValue(payload);
    let finish!: (value: SessionPayload) => void;
    vi.mocked(postSessionQuery).mockImplementation(
      (_sessionId, _content, handlers) => {
        handlers?.onToken?.("草稿文字");
        handlers?.onTokenReset?.();
        handlers?.onToken?.("熵是");
        return new Promise<SessionPayload>((resolve) => {
          finish = resolve;
        });
      },
    );

    await draw();
    await ask("继续");

    expect(host.textContent).toContain("熵是");
    expect(host.textContent).not.toContain("草稿文字");

    await ask("再来");
    expect(postSessionQuery).toHaveBeenCalledTimes(1);

    await act(async () => {
      finish(payload);
    });
    await act(async () => {});
  });

  it("tells the list when a turn is committed", async () => {
    const changed = vi.fn();
    vi.mocked(fetchSession).mockResolvedValue(payload);
    vi.mocked(postSessionQuery).mockResolvedValue({
      ...payload,
      messages: [
        ...payload.messages,
        {
          id: "e4",
          role: "user",
          content: "继续",
          createdAt: "2026-11-10T09:00:03Z",
        },
        {
          id: "e5",
          role: "assistant",
          content: "回答",
          createdAt: "2026-11-10T09:00:04Z",
        },
      ],
    });

    await draw("s1", undefined, undefined, changed);
    changed.mockClear();
    await ask("继续");

    expect(changed).toHaveBeenCalled();
    expect(
      changed.mock.calls.at(-1)?.[0].messages.at(-1)?.content,
    ).toBe("回答");
  });

  it("says what went wrong when the session cannot be read", async () => {
    vi.mocked(fetchSession).mockRejectedValue(new Error("boom"));

    await draw("missing");

    expect(host.textContent).toContain(i18n.t("chat.openFailed"));
  });

  it("says when the question was not answered", async () => {
    vi.mocked(fetchSession).mockResolvedValue(payload);
    vi.mocked(postSessionQuery).mockRejectedValue(new Error("boom"));

    await draw();
    await ask("继续");

    expect(host.textContent).toContain(i18n.t("chat.askFailed"));
  });

  it("says when earlier turns were summarized", async () => {
    vi.mocked(fetchSession).mockResolvedValue({
      ...payload,
      derived: {
        ...payload.derived,
        summary: "earlier turns were about entropy",
      },
    });

    await draw();

    expect(host.textContent).toContain(i18n.t("chat.compacted"));
  });

  it("marks an answer that was stopped before it finished", async () => {
    vi.mocked(fetchSession).mockResolvedValue({
      ...payload,
      messages: [
        ...payload.messages,
        {
          id: "e6",
          role: "assistant",
          content: "熵是信",
          createdAt: "2026-11-10T09:00:05Z",
          stopReason: "aborted",
        },
      ],
    });

    await draw();

    expect(host.textContent).toContain("熵是信");
    expect(host.textContent).toContain(i18n.t("chat.aborted"));
  });

  it("does not offer to keep an answer that was stopped", async () => {
    vi.mocked(fetchSession).mockResolvedValue({
      ...payload,
      messages: [
        payload.messages[0],
        {
          id: "e6",
          role: "assistant",
          content: "熵是信",
          createdAt: "2026-11-10T09:00:05Z",
          stopReason: "aborted",
        },
      ],
    });

    await draw();

    const save = [...host.querySelectorAll("button")].filter((button) =>
      button.textContent?.includes(i18n.t("chat.remark.save")),
    );
    expect(save).toHaveLength(0);
  });

  it("says when the session is still answering somewhere else", async () => {
    vi.mocked(fetchSession).mockResolvedValue({
      ...payload,
      state: { busy: true },
    });

    await draw();

    expect(host.textContent).toContain(i18n.t("chat.busy"));
    expect(host.querySelector("textarea")?.disabled).toBe(true);
  });

  it("words a refused question when the session is already answering", async () => {
    vi.mocked(fetchSession).mockResolvedValue(payload);
    vi.mocked(postSessionQuery).mockRejectedValue(
      new ApiError("busy", 409, "busy"),
    );

    await draw();
    await ask("继续");

    expect(host.textContent).toContain(i18n.t("error.code.busy"));
  });

  it("stops the answer on request and shows the part that was written", async () => {
    const stopped: SessionPayload = {
      ...payload,
      messages: [
        ...payload.messages,
        {
          id: "e6",
          role: "assistant",
          content: "熵是信",
          createdAt: "2026-11-10T09:00:05Z",
          stopReason: "aborted",
        },
      ],
    };
    vi.mocked(fetchSession)
      .mockResolvedValueOnce(payload)
      .mockResolvedValue(stopped);
    let rejectQuery!: (reason: unknown) => void;
    vi.mocked(postSessionQuery).mockImplementation((_id, _content, handlers) => {
      handlers?.onToken?.("熵是信");
      return new Promise<SessionPayload>((_resolve, reject) => {
        rejectQuery = reject;
      });
    });
    vi.mocked(postSessionCancel).mockResolvedValue({ cancelled: true });

    await draw();
    await ask("继续");
    expect(host.textContent).toContain("熵是信");

    const stop = [...host.querySelectorAll("button")].find((button) =>
      button.textContent?.includes(i18n.t("chat.stop")),
    );
    expect(stop).not.toBeUndefined();
    await act(async () => {
      stop?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(postSessionCancel).toHaveBeenCalledWith("s1");

    await act(async () => {
      rejectQuery(new ApiError("cancelled", -1, "cancelled"));
    });
    await act(async () => {});

    expect(host.textContent).toContain(i18n.t("chat.aborted"));
    expect(host.textContent).not.toContain(i18n.t("error.code.cancelled"));
  });

  it("shows what the answer looked at and opens a page from its card", async () => {
    const onOpenLink = vi.fn();
    vi.mocked(fetchSession).mockResolvedValue({
      ...payload,
      messages: [
        ...payload.messages,
        {
          id: "e6",
          role: "assistant",
          content: "熵衡量不确定性。",
          createdAt: "2026-11-10T09:00:05Z",
          cards: [
            {
              tool: "wiki_read",
              title: "Entropy card",
              path: "wiki/concepts/entropy.md",
              slug: "concepts/entropy",
            },
            {
              tool: "search",
              title: "Uncertainty hits",
              query: "不确定性",
              scope: "wiki",
              hits: 2,
            },
            {
              tool: "search",
              title: "Omitted hits",
              query: "熵",
              scope: "wiki",
              hits: 20,
              omitted: "budget",
              truncated: true,
            },
            {
              tool: "wiki_read",
              title: "Missing card",
              target: "[[nope]]",
              missing: true,
            },
            {
              tool: "wiki_read",
              title: "Long page card",
              path: "wiki/long.md",
              truncated: true,
            },
          ],
        },
      ],
    });

    await draw("s1", onOpenLink);

    expect(host.textContent).toContain("Entropy card");
    expect(host.textContent).toContain("Uncertainty hits");
    expect(host.textContent).toContain(i18n.t("chat.looked.hits", { count: 2 }));
    expect(host.textContent).toContain(i18n.t("chat.looked.missing"));
    expect(host.textContent).toContain(i18n.t("chat.looked.truncated"));
    expect(host.textContent).toContain(i18n.t("chat.looked.omitted"));

    const card = [...host.querySelectorAll("button")].find((button) =>
      button.textContent?.includes("Entropy card"),
    );
    expect(card).not.toBeUndefined();
    await act(async () => {
      card?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(onOpenLink).toHaveBeenCalledWith("concepts/entropy");
  });

  it("shows a card while the run is still going", async () => {
    vi.mocked(fetchSession).mockResolvedValue(payload);
    let finish!: (value: SessionPayload) => void;
    vi.mocked(postSessionQuery).mockImplementation(
      (_sessionId, _content, handlers) => {
        handlers?.onObservation?.({
          tool: "wiki_read",
          title: "Entropy live card",
          path: "wiki/concepts/entropy.md",
        });
        handlers?.onToken?.("熵衡量");
        return new Promise<SessionPayload>((resolve) => {
          finish = resolve;
        });
      },
    );

    await draw();
    await ask("继续");

    expect(host.textContent).toContain("Entropy live card");
    expect(host.textContent).toContain("熵衡量");

    await act(async () => {
      finish(payload);
    });
    await act(async () => {});
  });

  it("shows the wait card and opens the interface that owns the job", async () => {
    const onOpenInterface = vi.fn();
    vi.mocked(fetchSession).mockResolvedValue({
      ...payload,
      wait: proposedWait,
    });

    await draw("s1", undefined, onOpenInterface);

    expect(host.textContent).toContain(i18n.t("chat.wait.title"));
    expect(host.textContent).toContain(i18n.t("chat.wait.completion.start_plan"));
    expect(host.textContent).toContain(
      i18n.t("chat.wait.where", {
        place: i18n.t("chat.wait.interface.source"),
      }),
    );
    expect(host.textContent).toContain(proposedWait.intent);

    const open = [...host.querySelectorAll("button")].find((button) =>
      button.textContent?.includes(i18n.t("chat.wait.open.source")),
    );
    expect(open).not.toBeUndefined();
    await act(async () => {
      open?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(onOpenInterface).toHaveBeenCalledWith(proposedWait, "s1");
  });

  it("draws a compile wait with the board it belongs to", async () => {
    vi.mocked(fetchSession).mockResolvedValue({
      ...payload,
      wait: {
        ...proposedWait,
        job: "compile",
        interface: "plan_board",
        completion: "start_compile",
      },
    });

    await draw();

    expect(host.textContent).toContain(i18n.t("chat.wait.job.compile"));
    expect(host.textContent).toContain(
      i18n.t("chat.wait.interface.plan_board"),
    );
    expect(host.textContent).toContain(
      i18n.t("chat.wait.completion.start_compile"),
    );
    expect(host.textContent).toContain(i18n.t("chat.wait.open.plan_board"));
  });

  it("shows what a finished job produced", async () => {
    vi.mocked(fetchSession).mockResolvedValue({
      ...payload,
      wait: {
        ...proposedWait,
        status: "completed",
        resultSummary: "planned 2 pages",
        artifacts: [
          { kind: "write", id: "concepts/entropy", label: "Entropy" },
          {
            kind: "mention",
            id: "concepts/information",
            label: "Information",
          },
        ],
      },
    });

    await draw();

    expect(host.textContent).toContain(i18n.t("chat.wait.result"));
    expect(host.textContent).toContain(
      i18n.t("chat.wait.resultCount", { count: 2 }),
    );
    expect(host.textContent).toContain("Entropy");
    expect(host.textContent).toContain("Information");
    expect(host.textContent).toContain(i18n.t("chat.wait.artifact.write"));
    expect(host.textContent).toContain(i18n.t("chat.wait.artifact.mention"));
    expect(host.textContent).not.toContain(i18n.t("chat.wait.stop"));
  });

  it("stops a proposed wait and shows it cancelled", async () => {
    const cancelled = { ...proposedWait, status: "cancelled" };
    vi.mocked(fetchSession)
      .mockResolvedValueOnce({ ...payload, wait: proposedWait })
      .mockResolvedValue({ ...payload, wait: cancelled });
    vi.mocked(postSessionWaitCancel).mockResolvedValue({
      cancelled: true,
      wait: cancelled,
    });

    await draw();

    const stop = [...host.querySelectorAll("button")].find((button) =>
      button.textContent?.includes(i18n.t("chat.wait.stop")),
    );
    expect(stop).not.toBeUndefined();
    await act(async () => {
      stop?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await act(async () => {});

    expect(postSessionWaitCancel).toHaveBeenCalledWith("s1", "wait-1");
    expect(host.textContent).toContain(i18n.t("chat.wait.status.cancelled"));
    expect(
      [...host.querySelectorAll("button")].some((button) =>
        button.textContent?.includes(i18n.t("chat.wait.stop")),
      ),
    ).toBe(false);
  });

  it("does not carry a stop in flight into the next session", async () => {
    vi.mocked(fetchSession).mockImplementation((id) =>
      Promise.resolve(
        id === "s1"
          ? { ...payload, id: "s1", wait: proposedWait }
          : {
              ...payload,
              id: "s2",
              wait: { ...proposedWait, waitId: "wait-2" },
            },
      ),
    );
    // The stop never answers, so its state would leak if the next session
    // did not clear it.
    vi.mocked(postSessionWaitCancel).mockReturnValue(new Promise(() => {}));

    await draw("s1");
    const stop = [...host.querySelectorAll("button")].find((button) =>
      button.textContent?.includes(i18n.t("chat.wait.stop")),
    );
    expect(stop).not.toBeUndefined();
    await act(async () => {
      stop?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(host.textContent).toContain(i18n.t("chat.wait.stopping"));

    await draw("s2");

    const nextStop = [...host.querySelectorAll("button")].find((button) =>
      button.textContent?.includes(i18n.t("chat.wait.stop")),
    );
    expect(nextStop).not.toBeUndefined();
    expect((nextStop as HTMLButtonElement).disabled).toBe(false);
  });

  it("shows a wait proposed while the run is still going", async () => {
    vi.mocked(fetchSession).mockResolvedValue(payload);
    let finish!: (value: SessionPayload) => void;
    vi.mocked(postSessionQuery).mockImplementation(
      (_sessionId, _content, handlers) => {
        handlers?.onWait?.(proposedWait);
        return new Promise<SessionPayload>((resolve) => {
          finish = resolve;
        });
      },
    );

    await draw();
    await ask("安排一下");

    expect(host.textContent).toContain(i18n.t("chat.wait.title"));
    expect(host.textContent).toContain(i18n.t("chat.wait.completion.start_plan"));

    await act(async () => {
      finish({ ...payload, wait: proposedWait });
    });
    await act(async () => {});
    expect(host.textContent).toContain(i18n.t("chat.wait.title"));
  });

  it("does not offer to stop a wait whose job already started", async () => {
    vi.mocked(fetchSession).mockResolvedValue({
      ...payload,
      wait: { ...proposedWait, status: "entered" },
    });

    await draw();

    expect(host.textContent).toContain(i18n.t("chat.wait.enteredHint"));
    expect(
      [...host.querySelectorAll("button")].some((button) =>
        button.textContent?.includes(i18n.t("chat.wait.stop")),
      ),
    ).toBe(false);
  });
});
