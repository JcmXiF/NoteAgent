import { coreDetail, errorMessage, isSseAborted } from "@/lib/api/client";
import {
  useActivityStore,
  type ActivityKind,
  type ActivityStep,
  type StartActivity,
} from "@/stores/activity-store";
import type { ModuleId } from "@/modules";
import i18n from "@/i18n";

type RunContext = {
  /** Reports progress; the backend's `status` events map straight onto this. */
  status: (message: string, stepId?: string) => void;
  /** Appends a log line to the job. */
  note: (line: string) => void;
  /** Aborted when the user stops the job from the jobs panel. */
  signal: AbortSignal;
};

type RunOptions<T> = {
  kind: ActivityKind;
  title: string;
  module?: ModuleId;
  message?: string;
  steps?: ActivityStep[];
  run: (ctx: RunContext) => Promise<T>;
  /** Files the job wrote, so the panel can link to them. */
  filesWritten?: (result: T) => string[];
  /** Replaces the progress line once the job succeeds. */
  doneMessage?: (result: T) => string | undefined;
  /** Supplies the "try again" action shown when the job fails. */
  retry?: () => void;
};

/**
 * Runs a long job in the jobs panel.
 *
 * Registering here — rather than tracking a busy flag per view — is what keeps
 * a job visible after the user navigates away, and what makes stopping and
 * retrying available from one place.
 */
export async function runAsActivity<T>(options: RunOptions<T>): Promise<T> {
  const controller = new AbortController();
  const store = useActivityStore.getState();

  const input: StartActivity = {
    kind: options.kind,
    title: options.title,
    module: options.module,
    message: options.message,
    steps: options.steps,
    cancel: () => controller.abort(),
    retry: options.retry,
  };
  const id = store.start(input);

  const ctx: RunContext = {
    status: (message, stepId) =>
      useActivityStore.getState().setStatus(id, message, stepId),
    note: (line) => useActivityStore.getState().appendDetail(id, line),
    signal: controller.signal,
  };

  try {
    const result = await options.run(ctx);
    useActivityStore.getState().finish(id, {
      message: options.doneMessage?.(result),
      filesWritten: options.filesWritten?.(result),
    });
    return result;
  } catch (err) {
    const activity = useActivityStore.getState();
    if (isSseAborted(err, controller.signal)) {
      activity.markCancelled(id);
    } else {
      // Wording written for a log has no place on a screen: it goes to this job's
      // log, and the reader gets a sentence that was written for them.
      const said = coreDetail(err);
      if (said) activity.appendDetail(id, said);
      activity.fail(id, errorMessage(err, i18n.t("activity.failed")));
    }
    throw err;
  }
}
