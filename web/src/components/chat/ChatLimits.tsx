import { useTranslation } from "react-i18next";
import { ChevronRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { CHAT_LIMIT_GROUPS, outOfRange, sameLimits, valueFromInput } from "@/lib/chat-limits";
import { cn } from "@/lib/utils";
import { useChatStore } from "@/stores/chat-store";

/**
 * The chat's tuning knobs, folded away.
 *
 * Twelve numbers arranged flat in a sidebar would be the loudest thing in it, so
 * they live behind one line and are grouped by what they affect: this
 * conversation, what the assistant is told about the project, and what it may read
 * while answering.
 *
 * The values are shown as the core has them; anything typed stays a draft until it
 * is saved or thrown away, and a number the core would refuse is said so here
 * rather than sent.
 */
export function ChatLimits() {
  const { t } = useTranslation();
  const open = useChatStore((state) => state.settingsOpen);
  const saved = useChatStore((state) => state.limits);
  const draft = useChatStore((state) => state.limitsDraft);
  const bounds = useChatStore((state) => state.limitsBounds);
  const saving = useChatStore((state) => state.savingLimits);
  const error = useChatStore((state) => state.limitsError);
  const setSettingsOpen = useChatStore((state) => state.setSettingsOpen);
  const setLimit = useChatStore((state) => state.setLimit);
  const saveLimits = useChatStore((state) => state.saveLimits);
  const discardLimits = useChatStore((state) => state.discardLimits);

  const bad = draft ? outOfRange(draft, bounds) : [];
  const changed = !sameLimits(saved, draft);
  const canSave = draft !== null && changed && bad.length === 0 && !saving;

  return (
    <div className="shrink-0 border-t">
      <button
        type="button"
        className="flex w-full items-center gap-1 px-2 py-1.5 text-left text-[11px] font-medium hover:bg-accent/40"
        aria-expanded={open}
        onClick={() => setSettingsOpen(!open)}
      >
        <ChevronRight
          className={cn("size-3 shrink-0 transition-transform", open && "rotate-90")}
        />
        {t("chat.limits.title")}
        {changed && !open ? (
          // Said while folded, because the change is easy to forget about.
          <span className="ml-auto shrink-0 rounded bg-muted px-1 text-[10px] font-normal text-muted-foreground">
            {t("chat.limits.unsaved")}
          </span>
        ) : null}
      </button>

      {open ? (
        <div className="scroll-thin max-h-80 overflow-y-auto px-2 pb-2">
          <p className="pb-1.5 text-[10px] leading-relaxed text-muted-foreground">
            {t("chat.limits.intro")}
          </p>

          {draft === null ? (
            <p className="py-2 text-[11px] text-muted-foreground">
              {t("chat.limits.loading")}
            </p>
          ) : (
            <div className="flex flex-col gap-2.5">
              {CHAT_LIMIT_GROUPS.map((group) => (
                <fieldset key={group.title} className="flex flex-col gap-1.5">
                  <legend className="pb-0.5 text-[10px] font-semibold text-muted-foreground">
                    {t(group.title)}
                  </legend>
                  {group.fields.map((field) => {
                    const span = bounds?.[field.key];
                    const invalid = bad.includes(field.key);
                    return (
                      <label key={field.key} className="flex flex-col gap-0.5">
                        <span className="text-[11px]">{t(field.label)}</span>
                        <span className="flex items-center gap-1">
                          <Input
                            type="number"
                            inputMode="numeric"
                            min={span?.min}
                            max={span?.max}
                            step={1}
                            value={draft[field.key]}
                            disabled={saving}
                            aria-invalid={invalid || undefined}
                            aria-label={t(field.label)}
                            className={cn(
                              "h-7 text-[11px]",
                              invalid && "border-destructive focus-visible:border-destructive",
                            )}
                            onChange={(event) =>
                              setLimit(field.key, valueFromInput(event.target.value))
                            }
                          />
                          <span className="shrink-0 text-[10px] text-muted-foreground">
                            {t(field.unit)}
                          </span>
                        </span>
                        {invalid && span ? (
                          <span className="text-[10px] text-destructive">
                            {t("chat.limits.range", { min: span.min, max: span.max })}
                          </span>
                        ) : field.hint ? (
                          <span className="text-[10px] leading-snug text-muted-foreground">
                            {t(field.hint)}
                          </span>
                        ) : null}
                      </label>
                    );
                  })}
                </fieldset>
              ))}
            </div>
          )}

          {error ? <p className="pt-1.5 text-[11px] text-destructive">{error}</p> : null}

          <div className="flex items-center gap-1.5 pt-2">
            <Button size="sm" disabled={!canSave} onClick={() => void saveLimits()}>
              {saving ? t("chat.limits.saving") : t("chat.limits.save")}
            </Button>
            <Button
              variant="ghost"
              size="sm"
              disabled={!changed || saving}
              onClick={discardLimits}
            >
              {t("chat.limits.discard")}
            </Button>
            {changed && bad.length === 0 ? (
              <span className="ml-auto shrink-0 text-[10px] text-muted-foreground">
                {t("chat.limits.ready")}
              </span>
            ) : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}
