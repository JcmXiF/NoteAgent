import { create } from "zustand";
import {
  createChat,
  deleteChat,
  fetchChat,
  fetchChatLimits,
  fetchChats,
  putChatLimits,
  queryChat,
  renameChat,
} from "@/lib/api/chat";
import { errorMessage, isSseAborted, type ObservationPayload } from "@/lib/api/client";
import type {
  ChatConversation,
  ChatLimitBounds,
  ChatLimits,
  ChatListPayload,
  ChatSummary,
} from "@/lib/api/types";
import { activityKey, furthestStep, questionIsUnsent, type PendingTurn } from "@/lib/chat";
import { limitValues, sameLimits } from "@/lib/chat-limits";
import { runAsActivity } from "@/lib/run";
import { useClipsStore } from "@/stores/clips-store";
import { useDocsStore } from "@/stores/docs-store";
import { useWorkspaceStore } from "@/stores/workspace-store";
import i18n from "@/i18n";

/**
 * The conversations in this project, and which one is open.
 *
 * The list and the open conversation are one piece of state because they cannot be
 * right on their own: a conversation that is open but missing from the list is a
 * thing no view can draw.
 *
 * A project with no conversations gets one. Asking a question is the point of the
 * module, and an empty box with a "new conversation" button would be asking the
 * reader to set up the room before they can talk in it.
 */

type ChatState = {
  project: string | null;
  status: "idle" | "loading" | "ready" | "error";
  /** Something that went wrong with the list or the conversation itself. */
  error: string | null;

  conversations: ChatSummary[];
  /** The project's saved limits, as the core has them. */
  limits: ChatLimits | null;
  /** How far each knob may be turned, as the core reports it. */
  limitsBounds: ChatLimitBounds | null;
  /** The values the settings area is showing, which may not be saved yet. */
  limitsDraft: ChatLimits | null;
  /** Whether the settings area is open. */
  settingsOpen: boolean;
  /** Whether the draft is being written to the project. */
  savingLimits: boolean;
  /** Something that went wrong saving the limits. */
  limitsError: string | null;
  /** The conversation in front, and its messages once they have been read. */
  currentId: string | null;
  open: ChatConversation | null;

  /** What the reader has typed but not sent, kept so leaving does not lose it. */
  draft: string;
  setDraft: (text: string) => void;

  /** The turn in flight, or null. Its question and answer are not saved yet. */
  pending: PendingTurn | null;
  /**
   * What the assistant looked at for the turn in front of the reader.
   *
   * Kept here rather than in the turn being answered, because the moment it is worth
   * most is after the answer has arrived: the answer names pages, and this is what
   * makes them openable. The project stores questions and answers, so this lasts as
   * long as the turn stays on screen.
   */
  looked: ObservationPayload[];
  /** Why the last turn failed, shown where it was asked rather than in the list. */
  turnError: string | null;
  /**
   * The question whose turn failed, kept so it can be asked again.
   *
   * A failure can happen after the core has the question, so the draft is not always
   * the way back to it; this is the question itself either way.
   */
  failedQuestion: string | null;
  /**
   * Whether the last turn was stopped rather than finished.
   *
   * The core saves a question before it starts answering, so stopping leaves the
   * question in the conversation and no answer after it, which needs saying.
   */
  stopped: boolean;

  /** Reads the list, making a first conversation when there is none. */
  load: (project: string) => Promise<void>;
  /** Opens a conversation, reading its messages. */
  openConversation: (id: string) => Promise<void>;
  create: () => Promise<void>;
  /**
   * Names a conversation, or gives it back the name it had.
   *
   * An empty name is the reader's way back to the automatic one, so it is sent as
   * emptiness rather than refused; the core decides what an unnamed conversation is
   * called.
   */
  rename: (id: string, title: string) => Promise<void>;
  /** Deletes a conversation; false when it did not happen, with `error` saying why. */
  remove: (id: string) => Promise<boolean>;
  /** Asks a question in the conversation in front and writes the answer as it comes. */
  ask: (content: string) => Promise<void>;
  /** Asks the question whose turn failed again, as a fresh try. */
  retry: () => Promise<void>;

  /**
   * Stops waiting on the answer being written.
   *
   * The model call already under way may still finish; the core stops before the
   * next one, and nothing it wrote is added to the turn.
   */
  stop: () => void;

  /** Opens or closes the settings area, reading the limits the first time. */
  setSettingsOpen: (open: boolean) => void;
  /** Changes one knob in the draft. */
  setLimit: (key: keyof ChatLimits, value: number) => void;
  /** Writes the draft to the project, or does nothing if it is not usable. */
  saveLimits: () => Promise<void>;
  /** Throws the draft away and shows what the project has. */
  discardLimits: () => void;
  /**
   * Replaces the open conversation with what a turn just produced.
   *
   * Kept here rather than owned by the message view, so the list's title and count
   * follow a turn without the two views having to agree about anything.
   */
  setOpen: (conversation: ChatConversation) => void;
  reset: () => void;
};

/** Remembered per project, so reopening a project returns to the same conversation. */
const KEY_PREFIX = "noteagent.chat.";

function readLast(project: string): string {
  try {
    return localStorage.getItem(`${KEY_PREFIX}${project}`) ?? "";
  } catch {
    return "";
  }
}

function writeLast(project: string, id: string): void {
  try {
    localStorage.setItem(`${KEY_PREFIX}${project}`, id);
  } catch {
    // A browser refusing storage is not a reason to lose the conversation.
  }
}

function sorted(items: ChatSummary[]): ChatSummary[] {
  return [...items].sort((left, right) => right.updatedAt.localeCompare(left.updatedAt));
}

function summaryOf(conversation: ChatConversation): ChatSummary {
  return {
    id: conversation.id,
    title: conversation.title,
    updatedAt: conversation.updatedAt,
    messageCount: conversation.messages.length,
  };
}

export const useChatStore = create<ChatState>((set, get) => {
  /**
   * The turn being read, held here rather than in the view so switching modules
   * does not cancel it. It is cancelled only when the reader moves to another
   * conversation or project, where its answer would belong nowhere.
   */
  let reading: AbortController | null = null;

  function stopReading(): void {
    reading?.abort();
    reading = null;
    if (get().pending) set({ pending: null });
    set({ looked: [], failedQuestion: null });
  }

  /** Reads a conversation and puts it in front. A turn elsewhere is abandoned. */
  async function show(project: string, id: string): Promise<void> {
    stopReading();
    set({ turnError: null, stopped: false, draft: "" });
    if (!id) {
      set({ currentId: null, open: null });
      return;
    }
    try {
      const open = await fetchChat(project, id);
      if (get().project !== project) return;
      set({ currentId: id, open });
      writeLast(project, id);
    } catch (err) {
      // The conversation may have been removed elsewhere; the list is the truth.
      if (get().project !== project) return;
      set({
        currentId: null,
        open: null,
        error: errorMessage(err, i18n.t("chat.openFailed")),
      });
    }
  }

  /**
   * Reads back what a turn left behind.
   *
   * A turn can write pages and propose selections, so what it changed is read
   * again afterwards. Failure is not reported here: the answer is already on
   * screen, and a stale list corrects itself on the next open.
   */
  async function refreshAfterTurn(): Promise<void> {
    await Promise.allSettled([
      useDocsStore.getState().refresh(),
      useClipsStore.getState().reload(),
    ]);
  }

  return {
    project: null,
    status: "idle",
    error: null,
    conversations: [],
    limits: null,
    currentId: null,
    open: null,
    draft: "",
    pending: null,
    looked: [],
    turnError: null,
    failedQuestion: null,
    stopped: false,
    limitsDraft: null,
    limitsBounds: null,
    settingsOpen: false,
    savingLimits: false,
    limitsError: null,

    setDraft: (text) => set({ draft: text }),

    load: async (project) => {
      if (!project) return;
      if (get().project === project && get().status === "ready") return;
      stopReading();
      set({ project, status: "loading", error: null, turnError: null, stopped: false, draft: "" });
      let payload: ChatListPayload;
      try {
        payload = await fetchChats(project);
      } catch (err) {
        if (get().project !== project) return;
        set({ status: "error", error: errorMessage(err, i18n.t("chat.listFailed")) });
        return;
      }
      if (get().project !== project) return;
      const values = limitValues(payload.limits);
      set({ limits: values, limitsDraft: values, limitsBounds: payload.limits?.bounds ?? null });
      let conversations = sorted(payload.conversations ?? []);
      if (conversations.length === 0) {
        // A project with nowhere to talk in gets one, once: this only runs when the
        // list came back empty, so a reload is not a second conversation.
        try {
          const created = await createChat(project);
          if (get().project !== project) return;
          conversations = [summaryOf(created)];
          set({
            conversations,
            status: "ready",
            error: null,
            currentId: created.id,
            open: created,
          });
          writeLast(project, created.id);
          return;
        } catch (err) {
          if (get().project !== project) return;
          set({
            conversations: [],
            status: "ready",
            error: errorMessage(err, i18n.t("chat.createFailed")),
          });
          return;
        }
      }
      set({ conversations, status: "ready", error: null });
      const remembered = readLast(project);
      const wanted = conversations.some((item) => item.id === remembered)
        ? remembered
        : conversations[0].id;
      await show(project, wanted);
    },

    openConversation: async (id) => {
      const project = get().project;
      if (!project || id === get().currentId) return;
      await show(project, id);
    },

    create: async () => {
      const project = get().project;
      if (!project) return;
      try {
        const created = await createChat(project);
        if (get().project !== project) return;
        set((state) => ({
          conversations: sorted([summaryOf(created), ...state.conversations]),
          error: null,
        }));
        await show(project, created.id);
      } catch (err) {
        set({ error: errorMessage(err, i18n.t("chat.createFailed")) });
      }
    },

    rename: async (id, title) => {
      const project = get().project;
      if (!project) return;
      try {
        const renamed = await renameChat(project, id, title);
        if (get().project !== project) return;
        set((state) => ({
          error: null,
          // The pane in front is showing one title and the list another otherwise.
          open: state.open?.id === id ? { ...state.open, title: renamed.title } : state.open,
          conversations: sorted(
            state.conversations.map((item) =>
              item.id === id ? { ...item, title: renamed.title } : item,
            ),
          ),
        }));
      } catch (err) {
        set({ error: errorMessage(err, i18n.t("chat.renameFailed")) });
      }
    },

    remove: async (id) => {
      const project = get().project;
      if (!project) return false;
      try {
        const payload = await deleteChat(project, id);
        if (get().project !== project) return false;
        const conversations = sorted(payload.conversations ?? []);
        set({ conversations, error: null });
        if (get().currentId !== id) return true;
        // The conversation in front was the one removed, so the next one takes its
        // place rather than leaving an empty pane behind.
        set({ currentId: null, open: null });
        if (conversations.length > 0) await show(project, conversations[0].id);
        return true;
      } catch (err) {
        set({ error: errorMessage(err, i18n.t("chat.deleteFailed")) });
        return false;
      }
    },

    retry: async () => {
      const question = get().failedQuestion;
      if (!question) return;
      // The core skips a question it already has as the last turn, so asking again is
      // one try rather than a second question.
      await get().ask(question);
    },

    ask: async (content) => {
      const { project, open, pending } = get();
      if (!project || !open || pending) return;
      const question = content.trim();
      if (!question) return;
      const id = open.id;
      const controller = new AbortController();
      reading = controller;
      set({
        pending: { question, answer: "", step: null },
        // A new question is a new turn, and what the last one looked at goes with it.
        looked: [],
        turnError: null,
        failedQuestion: null,
        stopped: false,
        error: null,
        draft: "",
      });

      /** Guards against a turn that finished after the reader moved on. */
      const stillHere = () => get().project === project && get().currentId === id;

      try {
        const saved = await runAsActivity<ChatConversation>({
          kind: "chat",
          title: open.title || i18n.t("chat.untitled"),
          module: "chat",
          message: i18n.t(activityKey(null)),
          // The turn's three phases, so the jobs panel says where it has got to
          // without the reader having to watch the pane it was asked in.
          steps: [
            { id: "think", label: i18n.t("chat.step.think"), state: "current" },
            { id: "tool", label: i18n.t("chat.step.tool"), state: "pending" },
            { id: "answer", label: i18n.t("chat.step.answer"), state: "pending" },
          ],
          doneMessage: () => i18n.t("chat.answered"),
          run: async (ctx) => {
            // Stopping from the jobs panel and stopping from the pane are the same
            // stop, so either one ends the read.
            ctx.signal.addEventListener("abort", () => controller.abort());
            /**
             * Whether the track has been told an answer is being written. The core
             * only says so once it is saving, so the first token says it instead.
             */
            let writing = false;
            return queryChat(project, id, question, {
              onToken: (text) => {
                set((state) =>
                  state.pending
                    ? { pending: { ...state.pending, answer: state.pending.answer + text } }
                    : {},
                );
                if (!writing) {
                  writing = true;
                  const step = furthestStep(get().pending?.step ?? null, "answer");
                  set((state) =>
                    state.pending ? { pending: { ...state.pending, step } } : {},
                  );
                  ctx.status(i18n.t(activityKey(step)), step ?? undefined);
                }
              },
              onObservation: (item) =>
                // What the assistant looked at, kept beside the answer so the page it
                // names can be opened rather than only read about.
                set((state) => ({ looked: [...state.looked, item] })),
              onTokenReset: () =>
                // What this round wrote turned out not to be the answer.
                set((state) =>
                  state.pending ? { pending: { ...state.pending, answer: "" } } : {},
                ),
              onStatus: (message, stepId) => {
                // The core's own sentence stays out of the view: it is written for
                // the log, and the step it names is what the screen needs.
                if (message) ctx.note(stepId ? `${message} [${stepId}]` : message);
                const step = furthestStep(get().pending?.step ?? null, stepId ?? null);
                set((state) => (state.pending ? { pending: { ...state.pending, step } } : {}));
                ctx.status(i18n.t(activityKey(step)), step ?? undefined);
              },
              signal: controller.signal,
            });
          },
        });
        if (!stillHere()) return;
        set({ pending: null, turnError: null, failedQuestion: null });
        get().setOpen(saved);
        void refreshAfterTurn();
      } catch (err) {
        if (!stillHere()) return;
        const stopped = isSseAborted(err, controller.signal);
        // Nothing is carried over: the answer was never saved, and the question is
        // put back below only if the core turns out not to have it either.
        set({
          pending: null,
          turnError: stopped ? null : errorMessage(err, i18n.t("chat.askFailed")),
          // A stop is not a failure: the reader asked for it, and offering to ask
          // again would read as if something had gone wrong.
          failedQuestion: stopped ? null : question,
          stopped,
        });
        try {
          const refreshed = await fetchChat(project, id);
          if (!stillHere()) return;
          get().setOpen(refreshed);
          if (questionIsUnsent(refreshed.messages, question)) set({ draft: question });
        } catch {
          if (!stillHere()) return;
          set({ draft: question });
        }
      } finally {
        if (reading === controller) reading = null;
      }
    },

    stop: () => {
      reading?.abort();
    },

    setSettingsOpen: (open) => {
      set({ settingsOpen: open, limitsError: null });
      if (!open) return;
      const project = get().project;
      if (!project) return;
      // Read when it is opened rather than when the project is: the limits are the
      // core's, and this is the one moment the panel claims to show them. A draft
      // the reader has not saved yet is left alone — reading over it would throw
      // away what they typed, silently, which is the worst thing a settings area
      // can do. Only what the core has is refreshed underneath it.
      const keepDraft = !sameLimits(get().limits, get().limitsDraft);
      void (async () => {
        try {
          const limits = await fetchChatLimits(project);
          const values = limitValues(limits);
          if (get().project !== project) return;
          set(
            keepDraft
              ? { limits: values, limitsBounds: limits.bounds ?? null, limitsError: null }
              : {
                  limits: values,
                  limitsDraft: values,
                  limitsBounds: limits.bounds ?? null,
                  limitsError: null,
                },
          );
        } catch (err) {
          if (get().project !== project) return;
          set({ limitsError: errorMessage(err, i18n.t("chat.limits.loadFailed")) });
        }
      })();
    },

    setLimit: (key, value) =>
      set((state) =>
        state.limitsDraft
          ? { limitsDraft: { ...state.limitsDraft, [key]: value }, limitsError: null }
          : {},
      ),

    saveLimits: async () => {
      const project = get().project;
      const draft = get().limitsDraft;
      if (!project || !draft || get().savingLimits) return;
      set({ savingLimits: true, limitsError: null });
      try {
        const saved = await putChatLimits(project, draft);
        if (get().project !== project) return;
        // What was saved is what the core reports, not what was sent: bounds and
        // defaults are the core's to decide.
        const values = limitValues(saved);
        set({
          limits: values,
          limitsDraft: values,
          limitsBounds: saved.bounds ?? null,
          savingLimits: false,
        });
      } catch (err) {
        if (get().project !== project) return;
        set({
          savingLimits: false,
          limitsError: errorMessage(err, i18n.t("chat.limits.saveFailed")),
        });
      }
    },

    discardLimits: () => set((state) => ({ limitsDraft: state.limits, limitsError: null })),

    setOpen: (conversation) => {
      const project = get().project;
      set((state) => ({
        currentId: conversation.id,
        open: conversation,
        conversations: sorted(
          state.conversations.some((item) => item.id === conversation.id)
            ? state.conversations.map((item) =>
                item.id === conversation.id ? summaryOf(conversation) : item,
              )
            : [summaryOf(conversation), ...state.conversations],
        ),
      }));
      if (project) writeLast(project, conversation.id);
    },

    reset: () => {
      reading?.abort();
      reading = null;
      set({
        project: null,
        status: "idle",
        error: null,
        conversations: [],
        limits: null,
        limitsDraft: null,
        limitsBounds: null,
        settingsOpen: false,
        savingLimits: false,
        limitsError: null,
        currentId: null,
        open: null,
        draft: "",
        pending: null,
        looked: [],
        turnError: null,
        failedQuestion: null,
        stopped: false,
      });
    },
  };
});

/**
 * Follows the active project, the way the other stores do, so a view never has to
 * remember to load or clear.
 */
let started = false;

export function startChatSync(): void {
  if (started) return;
  started = true;

  useWorkspaceStore.subscribe((state, previous) => {
    if (state.current === previous.current) return;
    const chat = useChatStore.getState();
    if (state.current) void chat.load(state.current);
    else chat.reset();
  });

  const current = useWorkspaceStore.getState().current;
  if (current) void useChatStore.getState().load(current);
}
