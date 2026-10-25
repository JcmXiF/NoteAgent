/**
 * The single HTTP and SSE entry point.
 *
 * Requests go through the Vite proxy to the Python core. A refusal answers with a
 * sentence for the log plus, when the core knows which situation it is in, a
 * stable code and the figures a sentence needs; the interface says that code in
 * the reader's language. Anything the core did not name is kept in the log, and
 * the screen gets a sentence of its own.
 */

import i18n from "@/i18n";

const API_BASE = "/api";

export class ApiError extends Error {
  readonly status: number;
  /** The core's stable name for the situation, when it gave one. */
  readonly code: string;
  /** The figures the sentence for that code needs, as text. */
  readonly params: Record<string, string>;
  /** What the core said, for the log. Never shown as it stands. */
  readonly detail: string;

  constructor(
    message: string,
    status: number,
    code = "",
    params: Record<string, string> = {},
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.params = params;
    this.detail = message;
  }
}

/**
 * A failure the interface has already worded for the reader.
 *
 * Its message is shown as it is. Everything else needs a code from the core or a
 * sentence from the caller, so that no wording written for a log reaches a screen.
 */
export class ShownError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ShownError";
  }
}

/** A stream that failed after it started: there is no HTTP status to report. */
const STREAM_STATUS = -1;

/**
 * How the browser words a request that never arrived.
 *
 * The wording differs by engine and says nothing about what is wrong or where to
 * look, so it is recognised rather than shown. Requests the app makes itself are
 * translated where they are sent; this catches the rest.
 */
const UNREACHED_WORDS =
  /^(failed to fetch|load failed|fetch failed|network request failed|networkerror\b.*)$/i;

/** The status of a request that never reached the service. */
export const UNREACHED_STATUS = 0;

/** The statuses a gateway answers with when the thing behind it is not there. */
const UNREACHED_STATUSES = new Set([502, 503, 504]);

/** The sentence a code names, or an empty string when this build has none. */
function sentenceForCode(code: string, params: Record<string, string>): string {
  const key = `error.code.${code}`;
  if (!i18n.exists(key)) return "";
  const said = i18n.t(key, { ...params });
  return typeof said === "string" && said !== key ? said : "";
}

/**
 * Keeps the core's own words where they can be read, since the screen will not
 * carry them: the wording is written for whoever reads a log.
 */
function logDetail(detail: string): void {
  if (!detail) return;
  console.info(`[noteagent] the service said: ${detail}`);
}

/** What the core said, for a job's log. Empty for a sentence the interface wrote. */
export function coreDetail(err: unknown): string {
  if (err instanceof ShownError) return "";
  if (err instanceof ApiError) return err.detail;
  if (err instanceof Error) return err.message;
  return typeof err === "string" ? err : "";
}

export function errorMessage(err: unknown, fallback: string): string {
  if (err instanceof ShownError) return err.message;
  const raw =
    err instanceof Error && err.message
      ? err.message
      : typeof err === "string"
        ? err
        : "";
  if (err instanceof ApiError) {
    if (err.code) {
      // The core named the situation; that name wins over how the answer was
      // carried, because a provider that failed them is not a service that is
      // down.
      const said = sentenceForCode(err.code, err.params);
      if (said) return said;
      logDetail(raw);
      return fallback;
    }
    if (err.status === UNREACHED_STATUS) return i18n.t("error.offline");
    // A gateway in front of the core says "502" when the core is not running, which is
    // the same thing the reader would see as a browser network error — and neither
    // sentence is theirs.
    if (UNREACHED_STATUSES.has(err.status)) return i18n.t("error.offline");
    logDetail(raw);
    return fallback;
  }
  if (!raw) return fallback;
  if (UNREACHED_WORDS.test(raw.trim())) return i18n.t("error.offline");
  logDetail(raw);
  return fallback;
}

/** What the core answered with: the sentence for the log, its code, its figures. */
type CoreError = { message: string; code: string; params: Record<string, string> };

function stringMap(value: unknown): Record<string, string> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return {};
  const out: Record<string, string> = {};
  for (const [key, item] of Object.entries(value as Record<string, unknown>)) {
    if (typeof item === "string") out[key] = item;
  }
  return out;
}

function readPayload(payload: unknown, fallbackMessage: string): CoreError {
  if (payload === null || typeof payload !== "object" || Array.isArray(payload)) {
    return { message: fallbackMessage, code: "", params: {} };
  }
  const record = payload as Record<string, unknown>;
  const code = typeof record.code === "string" ? record.code : "";
  const params = stringMap(record.params);
  const detail = record.detail;
  if (typeof detail === "string" && detail) return { message: detail, code, params };
  return { message: fallbackMessage, code, params };
}

async function readError(res: Response): Promise<CoreError> {
  const text = await res.text().catch(() => "");
  if (!text) return { message: `HTTP ${res.status}`, code: "", params: {} };
  try {
    return readPayload(JSON.parse(text), text);
  } catch {
    /* not JSON */
  }
  return { message: text, code: "", params: {} };
}

/**
 * Sends a request, and says so when it never gets there.
 *
 * A core that is not running is the common case in desktop use, and the browser's
 * own error for it reads like a fault in the page. Callers abort on purpose as
 * well, and that is their own error, passed through untouched.
 */
async function send(url: string, init: RequestInit): Promise<Response> {
  try {
    return await fetch(url, init);
  } catch (err) {
    if (init.signal?.aborted) throw err;
    throw new ApiError(i18n.t("error.offline"), UNREACHED_STATUS);
  }
}

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
  init?: RequestInit,
): Promise<T> {
  const res = await send(`${API_BASE}${path}`, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    ...init,
  });
  if (!res.ok) {
    const failure = await readError(res);
    throw new ApiError(failure.message, res.status, failure.code, failure.params);
  }
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  if (!text) return undefined as T;
  return JSON.parse(text) as T;
}

export function getJson<T>(path: string, init?: RequestInit): Promise<T> {
  return request<T>("GET", path, undefined, init);
}

export function postJson<T>(
  path: string,
  body?: unknown,
  init?: RequestInit,
): Promise<T> {
  return request<T>("POST", path, body ?? {}, init);
}

export function putJson<T>(path: string, body?: unknown): Promise<T> {
  return request<T>("PUT", path, body ?? {});
}

export function patchJson<T>(path: string, body?: unknown): Promise<T> {
  return request<T>("PATCH", path, body ?? {});
}

export function deleteJson<T>(path: string, body?: unknown): Promise<T> {
  return request<T>("DELETE", path, body);
}

/** Project-scoped path helper: `projectPath("rl", "tree")` → `/projects/rl/tree`. */
export function projectPath(project: string, suffix = ""): string {
  const base = `/projects/${encodeURIComponent(project)}`;
  return suffix ? `${base}/${suffix}` : base;
}

/* ------------------------------------------------------------------ SSE */

function parseSseBlock(raw: string): { event: string; data: string } | null {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of raw.split("\n")) {
    if (line.startsWith("event:")) {
      event = line.slice(6).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).trimStart());
    }
  }
  if (dataLines.length === 0) return null;
  return { event, data: dataLines.join("\n") };
}

/** The server sends a keepalive every 15s; several missed ones mean a dead stream. */
const SSE_STALL_MS = 45_000;

function stallMessage(): string {
  return i18n.t("error.streamStalled");
}

/**
 * The reader stopped waiting at the user's request. The model call already in
 * flight may still finish; the backend stops before its next one.
 */
function abortedMessage(): string {
  return i18n.t("error.streamStopped");
}

export function isSseAborted(err: unknown, signal?: AbortSignal): boolean {
  if (signal?.aborted) return true;
  if (err instanceof Error && err.message === abortedMessage()) return true;
  if (typeof DOMException !== "undefined" && err instanceof DOMException) {
    if (err.name === "AbortError") return true;
  }
  return err instanceof Error && err.name === "AbortError";
}

function abortedError(): ShownError {
  return new ShownError(abortedMessage());
}

function readChunk(
  reader: ReadableStreamDefaultReader<Uint8Array>,
  signal?: AbortSignal,
): Promise<ReadableStreamReadResult<Uint8Array>> {
  if (signal?.aborted) return Promise.reject(abortedError());
  let timer: ReturnType<typeof setTimeout> | undefined;
  let onAbort: (() => void) | undefined;
  return Promise.race([
    reader.read(),
    new Promise<never>((_, reject) => {
      timer = setTimeout(() => reject(new ShownError(stallMessage())), SSE_STALL_MS);
      if (!signal) return;
      onAbort = () => reject(abortedError());
      signal.addEventListener("abort", onAbort, { once: true });
    }),
  ]).finally(() => {
    if (timer !== undefined) clearTimeout(timer);
    if (signal && onAbort) signal.removeEventListener("abort", onAbort);
  });
}

export type SseHandlers = {
  /** `status`: a progress line, optionally naming a step on the track. */
  onStatus?: (message: string, stepId?: string) => void;
  /** `token`: streamed text, sent only by chat. */
  onToken?: (text: string) => void;
  /**
   * A round that was not writing the answer takes back what it sent.
   *
   * Only chat sends this: a round that turns out to be a tool call may have written
   * a sentence on the way, and that sentence is not the answer.
   */
  onTokenReset?: () => void;
  /**
   * `observation`: something the assistant looked at, as data.
   *
   * Only chat sends this. It is the tool result without the text written for the
   * model, so what it touched can be shown as a card rather than only described in
   * the answer.
   */
  onObservation?: (observation: ObservationPayload) => void;
  /** Aborting this stops the reader; the backend cancels before its next model call. */
  signal?: AbortSignal;
  /** Message used when the stream ends without a `done` or `error` event. */
  emptyError?: string;
};

/**
 * One thing the assistant looked at.
 *
 * The shape is the core's: `tool` and `target` say what was touched and `title`
 * what to call it; the rest are the marks a tool left (where it is, whether it was
 * missing, whether it is a source). Everything is optional but the tool, because a
 * tool the interface has never heard of still reports itself.
 */
export type ObservationPayload = {
  tool: string;
  title?: string;
  target?: string;
  url?: string;
  path?: string;
  slug?: string;
  scope?: string;
  query?: string;
  match?: string;
  missing?: boolean;
  unsafe?: boolean;
  error?: boolean;
  omitted?: string;
  truncated?: boolean;
  not_source?: boolean;
  needs_human?: boolean;
  job?: string;
  fidelity?: string;
  http_status?: number;
  /** For a write action: which one it was, and whether it actually wrote. */
  action?: string;
  wrote_wiki?: boolean;
};

/**
 * POSTs a request and reads the SSE response, resolving with the `done` payload.
 * Every streaming endpoint goes through here.
 */
export async function postSse<T>(
  path: string,
  body: unknown,
  handlers: SseHandlers = {},
): Promise<T> {
  const fallbackError = i18n.t("error.noResult");
  const { onStatus, onToken, onTokenReset, onObservation, signal, emptyError = fallbackError } =
    handlers;
  if (signal?.aborted) throw abortedError();

  const res = await send(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
    signal,
  });
  if (!res.ok) {
    const failure = await readError(res);
    throw new ApiError(failure.message, res.status, failure.code, failure.params);
  }
  if (!res.body) throw new ShownError(i18n.t("error.noStream"));

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  let donePayload: T | null = null;
  let streamError: CoreError | null = null;

  try {
    for (;;) {
      const { value, done } = await readChunk(reader, signal);
      if (done) break;
      buf += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
      let sep = buf.indexOf("\n\n");
      while (sep >= 0) {
        const raw = buf.slice(0, sep);
        buf = buf.slice(sep + 2);
        const parsed = parseSseBlock(raw);
        if (parsed) {
          const payload = JSON.parse(parsed.data) as Record<string, unknown>;
          if (parsed.event === "status" && typeof payload.message === "string") {
            const stepId =
              typeof payload.stepId === "string" && payload.stepId
                ? payload.stepId
                : undefined;
            onStatus?.(payload.message, stepId);
          } else if (parsed.event === "observation") {
            if (typeof payload.tool === "string" && payload.tool) {
              onObservation?.(payload as unknown as ObservationPayload);
            }
          } else if (parsed.event === "token") {
            // A retraction carries no text of its own: it says the text already sent
            // was not the answer. An event without it is text as usual.
            if (payload.reset === true) onTokenReset?.();
            else if (typeof payload.text === "string") onToken?.(payload.text);
          } else if (parsed.event === "done") {
            donePayload = payload as unknown as T;
          } else if (parsed.event === "error") {
            streamError = readPayload(payload, emptyError);
          }
        }
        sep = buf.indexOf("\n\n");
      }
    }
  } catch (err) {
    try {
      await reader.cancel();
    } catch {
      /* already closed */
    }
    if (isSseAborted(err, signal)) throw abortedError();
    if (err instanceof ShownError && err.message === stallMessage()) throw err;
    throw new ShownError(stallMessage());
  }

  if (signal?.aborted) throw abortedError();
  if (streamError) {
    throw new ApiError(
      streamError.message,
      STREAM_STATUS,
      streamError.code,
      streamError.params,
    );
  }
  if (!donePayload) throw new ShownError(`${emptyError}. ${stallMessage()}`);
  return donePayload;
}
