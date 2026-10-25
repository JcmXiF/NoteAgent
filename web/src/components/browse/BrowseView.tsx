import { useEffect, useMemo, useRef } from "react";
import { useTranslation } from "react-i18next";
import { ArrowLeft, ArrowRight, Globe, Loader2, Plus, RotateCw, Undo2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { IconTip } from "@/components/ui/tooltip";
import { BrowseTabs } from "@/components/browse/BrowseTabs";
import { BrowseSignals } from "@/components/browse/BrowseSignals";
import { activeTab } from "@/lib/browse";
import { MODULE_META } from "@/modules";
import { useBrowseStore } from "@/stores/browse-store";
import { activePaneSlot, useLayoutStore, type PaneSlot } from "@/stores/layout-store";

/**
 * Reading a page in the shell's own browser.
 *
 * The page is not drawn here — the shell draws it as a surface over the window —
 * so this provides somewhere for it to sit and claims it, which is how the shell is
 * told where the page goes. The area is measured rather than worked out: it is
 * whatever is left of the pane below the bar, which stays true whatever the layout
 * around it does.
 */
export function BrowseView({ slot }: { slot: PaneSlot }) {
  const { t } = useTranslation();
  const available = useBrowseStore((state) => state.available);
  const connected = useBrowseStore((state) => state.connected);
  const guest = useBrowseStore((state) => state.guest);
  const address = useBrowseStore((state) => state.address);
  const error = useBrowseStore((state) => state.error);
  const attach = useBrowseStore((state) => state.attach);
  const setAddress = useBrowseStore((state) => state.setAddress);
  const setAddressFocused = useBrowseStore((state) => state.setAddressFocused);
  const navigate = useBrowseStore((state) => state.navigate);
  const newTab = useBrowseStore((state) => state.newTab);
  const back = useBrowseStore((state) => state.back);
  const forward = useBrowseStore((state) => state.forward);
  const reload = useBrowseStore((state) => state.reload);

  // The shell has one browser, so only the pane with focus can give it a place to
  // be. Two panes showing this module would otherwise both claim the same surface.
  const mine = useLayoutStore((state) => activePaneSlot(state) === slot);
  // A pane that swapped in place has somewhere to go back to.
  const returnTo = useLayoutStore((state) => state.paneReturn[slot] ?? null);
  const returnPaneModule = useLayoutStore((state) => state.returnPaneModule);

  const hostRef = useRef<HTMLDivElement | null>(null);
  const tab = useMemo(() => activeTab(guest.tabs, guest.activeId), [guest]);

  useEffect(() => attach(), [attach]);

  useClaimGuestArea(hostRef, slot, mine);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <BrowseTabs />
      <div className="flex shrink-0 items-center gap-1 border-b px-2 py-1">
        {returnTo ? (
          // A page that took the place of what was being read has to leave a way
          // back, or the reader has to find their place again.
          <Button
            variant="outline"
            size="sm"
            className="h-6 shrink-0 text-[11px]"
            onClick={() => returnPaneModule(slot)}
          >
            <Undo2 />
            {t("nav.backTo", { module: t(`${MODULE_META[returnTo].i18nKey}.label`) })}
          </Button>
        ) : null}
        <IconTip label={t("browse.back")}>
          <Button
            variant="ghost"
            size="icon-xs"
            disabled={!guest.canGoBack}
            onClick={() => void back()}
          >
            <ArrowLeft />
          </Button>
        </IconTip>
        <IconTip label={t("browse.forward")}>
          <Button
            variant="ghost"
            size="icon-xs"
            disabled={!guest.canGoForward}
            onClick={() => void forward()}
          >
            <ArrowRight />
          </Button>
        </IconTip>
        <IconTip label={t("browse.reload")}>
          <Button
            variant="ghost"
            size="icon-xs"
            disabled={!available}
            onClick={() => void reload()}
          >
            {tab?.loading ? <Loader2 className="animate-spin" /> : <RotateCw />}
          </Button>
        </IconTip>

        <form
          className="min-w-0 flex-1"
          onSubmit={(event) => {
            event.preventDefault();
            void navigate(address);
          }}
        >
          <Input
            value={address}
            disabled={!available}
            onChange={(event) => setAddress(event.target.value)}
            onFocus={() => setAddressFocused(true)}
            onBlur={() => setAddressFocused(false)}
            placeholder={t("browse.addressPlaceholder")}
            aria-label={t("browse.address")}
            className="h-7 text-[11px]"
            spellCheck={false}
          />
        </form>

        <IconTip label={t("browse.newTab")}>
          <Button
            variant="ghost"
            size="icon-xs"
            disabled={!available}
            onClick={() => void newTab()}
          >
            <Plus />
          </Button>
        </IconTip>
      </div>

      <BrowseSignals tab={tab} />

      {error ? (
        <p className="shrink-0 border-b bg-destructive/10 px-2 py-1 text-[11px] text-destructive">
          {error}
        </p>
      ) : null}

      {/*
        The page area. Whatever this measures as is what the shell is told, so it
        exists even when there is no browser to put in it.
      */}
      <div ref={hostRef} className="relative min-h-0 flex-1">
        {!available ? (
          <Notice
            icon={<Globe className="size-8 text-muted-foreground/25" />}
            title={t("browse.unavailable")}
            body={t("browse.unavailableHint")}
          />
        ) : !mine ? (
          <Notice title={t("browse.otherPane")} body={t("browse.otherPaneHint")} />
        ) : !connected ? (
          <Notice title={t("browse.connecting")} />
        ) : guest.tabs.length === 0 || !tab ? (
          <Notice
            icon={<Globe className="size-8 text-muted-foreground/25" />}
            title={t("browse.noPage")}
            body={t("browse.noPageHint")}
          />
        ) : null}
      </div>
    </div>
  );
}

/**
 * Offers the page area to the shell, while this is the pane showing the browser.
 *
 * Claiming rather than measuring: there is one browser and so one place for it, and
 * the pane that owns that place is the one with focus. `GuestHost` does the
 * measuring, so two panes cannot both decide where the page goes.
 */
function useClaimGuestArea(
  ref: React.RefObject<HTMLDivElement | null>,
  slot: PaneSlot,
  mine: boolean,
): void {
  useEffect(() => {
    if (!mine) return undefined;
    const element = ref.current;
    if (!element) return undefined;
    useBrowseStore.getState().claim(slot, element);
    return () => useBrowseStore.getState().release(slot);
  }, [ref, slot, mine]);
}

function Notice({
  icon,
  title,
  body,
}: {
  icon?: React.ReactNode;
  title: string;
  body?: string;
}) {
  return (
    <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 p-8 text-center">
      {icon}
      <p className="text-sm font-medium">{title}</p>
      {body ? <p className="max-w-sm text-xs text-muted-foreground">{body}</p> : null}
    </div>
  );
}
