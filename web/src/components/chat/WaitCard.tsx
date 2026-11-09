import { useTranslation } from "react-i18next";
import { ArrowUpRight, Clock3, Square } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { WaitPayload } from "@/lib/api/client";

/**
 * The one collaboration node a session is waiting on.
 *
 * The chat only proposes it: the job starts when the human presses the
 * original button in the interface named here, so the card's job is to say
 * where to go and to offer a way to stop before the job starts.
 */
export function WaitCard({
  wait,
  onOpen,
  onStop,
  stopping = false,
}: {
  wait: WaitPayload;
  /** Opening the interface that owns the job, with the wait it must carry. */
  onOpen?: (view: string, waitId: string) => void;
  /** Stop a wait the human has not entered; absent while it cannot be stopped. */
  onStop?: () => void;
  stopping?: boolean;
}) {
  const { t } = useTranslation();
  const place = t(`chat.wait.interface.${wait.interface}`, {
    defaultValue: wait.interface,
  });
  const completion = t(`chat.wait.completion.${wait.completion}`, {
    defaultValue: t("chat.wait.completion.start"),
  });
  const stop = wait.status === "proposed" && onStop !== undefined;

  return (
    <section
      aria-label={t("chat.wait.title")}
      className="mt-1.5 mb-3 rounded-md border bg-muted/30 p-2.5"
    >
      <div className="flex items-center gap-1.5 text-[11px]">
        <Clock3 className="size-3.5 shrink-0 text-muted-foreground" />
        <span className="font-medium">{t("chat.wait.title")}</span>
        <span className="ml-auto rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
          {t(`chat.wait.job.${wait.job}`, { defaultValue: wait.job })}
        </span>
      </div>
      <p className="mt-1.5 text-[12px]">{completion}</p>
      <p className="mt-0.5 text-[11px] text-muted-foreground">
        {t("chat.wait.where", { place })}
      </p>
      {wait.intent ? <p className="mt-1 text-[11px]">{wait.intent}</p> : null}
      <p className="mt-1 text-[11px] text-muted-foreground">
        {t(`chat.wait.status.${wait.status}`, { defaultValue: wait.status })}
      </p>
      {wait.status === "entered" ? (
        <p className="mt-0.5 text-[10px] text-muted-foreground">
          {t("chat.wait.enteredHint")}
        </p>
      ) : null}
      <div className="mt-2 flex items-center gap-1.5">
        <Button
          variant="outline"
          size="sm"
          onClick={() => onOpen?.(wait.interface, wait.waitId)}
        >
          <ArrowUpRight />
          {t(`chat.wait.open.${wait.interface}`, {
            defaultValue: t("chat.wait.open.default"),
          })}
        </Button>
        {stop ? (
          <Button variant="ghost" size="sm" disabled={stopping} onClick={onStop}>
            <Square />
            {stopping ? t("chat.wait.stopping") : t("chat.wait.stop")}
          </Button>
        ) : null}
      </div>
    </section>
  );
}
