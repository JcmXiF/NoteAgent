import { useTranslation } from "react-i18next";
import { AlertTriangle, CheckCircle2, Lock, Timer } from "lucide-react";
import { Button } from "@/components/ui/button";
import { IconTip } from "@/components/ui/tooltip";
import type { GuestTab } from "@/lib/desktop";
import { useBrowseStore } from "@/stores/browse-store";

/**
 * What the shell knows about the page in front: whether it loaded, whether the
 * reader is signed in, and whether keeping it would work.
 *
 * A page behind a sign-in looks like any other page, so the one thing worth saying
 * is that it needs signing in before anything can be kept from it. Cookies are
 * reported as a count and never as values — enough to say "this site knows you",
 * which is what makes keeping the page worthwhile.
 */
export function BrowseSignals({ tab }: { tab: GuestTab | null }) {
  const { t } = useTranslation();
  const keepPage = useBrowseStore((state) => state.keepPage);
  const available = useBrowseStore((state) => state.available);
  const connected = useBrowseStore((state) => state.connected);

  if (!available || !connected) return null;

  const status = tab?.httpStatus ?? null;
  const signedIn = Boolean(tab?.hasCookies);
  const cookieCount = tab?.cookieCount ?? 0;

  // Only what a reader would want to know: a page that loaded fine and keeps no
  // cookies needs no comment, and one that refused needs it before a capture.
  const blocked = status === 401 || status === 403;
  const rateLimited = status === 429;

  return (
    <div className="flex shrink-0 items-center gap-2 px-2 pb-1">
      {status === 401 ? (
        <span className="flex min-w-0 items-center gap-1 text-[10px] text-destructive">
          <Lock className="size-3 shrink-0" />
          {t("browse.signal.signIn")}
        </span>
      ) : status === 403 ? (
        <span className="flex min-w-0 items-center gap-1 text-[10px] text-destructive">
          <AlertTriangle className="size-3 shrink-0" />
          {t("browse.signal.forbidden")}
        </span>
      ) : rateLimited ? (
        <span className="flex min-w-0 items-center gap-1 text-[10px] text-muted-foreground">
          <Timer className="size-3 shrink-0" />
          {t("browse.signal.rateLimited")}
        </span>
      ) : signedIn ? (
        <span className="flex min-w-0 items-center gap-1 text-[10px] text-muted-foreground">
          <CheckCircle2 className="size-3 shrink-0 text-success" />
          {t("browse.signal.signedIn", { count: cookieCount })}
        </span>
      ) : (
        <span className="min-w-0 truncate text-[10px] text-muted-foreground">
          {t("browse.signal.anonymous")}
        </span>
      )}

      <span className="ml-auto shrink-0">
        <IconTip label={blocked ? t("browse.keep.blocked") : t("browse.keep.hint")}>
          <span>
            <Button
              size="sm"
              variant="outline"
              className="h-6 text-[11px]"
              disabled={!tab?.url || blocked}
              onClick={() => void keepPage()}
            >
              {t("browse.keep.run")}
            </Button>
          </span>
        </IconTip>
      </span>
    </div>
  );
}
