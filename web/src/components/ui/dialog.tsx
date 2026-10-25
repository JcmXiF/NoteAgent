import { createContext, useContext, useEffect } from "react";
import type * as React from "react";
import { Dialog as DialogPrimitive } from "@base-ui/react/dialog";
import { XIcon } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { useLayoutStore } from "@/stores/layout-store";

/**
 * Whether the dialog it belongs to is open.
 *
 * A dialog's content is rendered even while it is closed, so being mounted says
 * nothing about being visible — and the guest page has to move only while the
 * dialog is actually in front.
 */
const DialogOpenContext = createContext(false);

function Dialog({ open, defaultOpen, ...props }: DialogPrimitive.Root.Props) {
  const shown = open ?? defaultOpen ?? false;
  return (
    <DialogOpenContext.Provider value={shown}>
      <DialogPrimitive.Root data-slot="dialog" open={open} defaultOpen={defaultOpen} {...props} />
    </DialogOpenContext.Provider>
  );
}

function DialogTrigger(props: DialogPrimitive.Trigger.Props) {
  return <DialogPrimitive.Trigger data-slot="dialog-trigger" {...props} />;
}

function DialogClose(props: DialogPrimitive.Close.Props) {
  return <DialogPrimitive.Close data-slot="dialog-close" {...props} />;
}

function DialogContent({
  className,
  children,
  showCloseButton = true,
  ...props
}: DialogPrimitive.Popup.Props & { showCloseButton?: boolean }) {
  const { t } = useTranslation();
  // A dialog is drawn by the page, and the shell's browser is drawn over the page,
  // so without this the dialog would open behind it and could not be read.
  const dialogOpen = useContext(DialogOpenContext);
  const holdGuest = useLayoutStore((state) => state.holdGuest);
  const releaseGuest = useLayoutStore((state) => state.releaseGuest);
  useEffect(() => {
    if (!dialogOpen) return undefined;
    holdGuest();
    return () => releaseGuest();
  }, [dialogOpen, holdGuest, releaseGuest]);
  return (
    <DialogPrimitive.Portal>
      <DialogPrimitive.Backdrop
        data-slot="dialog-overlay"
        className="fixed inset-0 z-50 bg-black/30 backdrop-blur-[2px] transition-opacity data-closed:opacity-0"
      />
      <DialogPrimitive.Popup
        data-slot="dialog-content"
        className={cn(
          "fixed top-1/2 left-1/2 z-50 grid w-full max-w-[calc(100%-2rem)] -translate-x-1/2 -translate-y-1/2 gap-4",
          "rounded-xl bg-popover p-4 text-sm text-popover-foreground ring-1 ring-foreground/10 outline-none transition-all",
          "data-closed:scale-95 data-closed:opacity-0 sm:max-w-md",
          className,
        )}
        {...props}
      >
        {children}
        {showCloseButton ? (
          <DialogPrimitive.Close
            data-slot="dialog-close"
            render={<Button variant="ghost" size="icon-sm" className="absolute top-2 right-2" />}
          >
            <XIcon />
            <span className="sr-only">{t("common.close")}</span>
          </DialogPrimitive.Close>
        ) : null}
      </DialogPrimitive.Popup>
    </DialogPrimitive.Portal>
  );
}

function DialogHeader({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="dialog-header"
      className={cn("flex flex-col gap-2", className)}
      {...props}
    />
  );
}

function DialogFooter({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="dialog-footer"
      className={cn(
        "-mx-4 -mb-4 flex flex-col-reverse gap-2 rounded-b-xl border-t bg-muted/50 p-4 sm:flex-row sm:justify-end",
        className,
      )}
      {...props}
    />
  );
}

function DialogTitle({ className, ...props }: DialogPrimitive.Title.Props) {
  return (
    <DialogPrimitive.Title
      data-slot="dialog-title"
      className={cn("font-heading text-base leading-none font-medium", className)}
      {...props}
    />
  );
}

function DialogDescription({
  className,
  ...props
}: DialogPrimitive.Description.Props) {
  return (
    <DialogPrimitive.Description
      data-slot="dialog-description"
      className={cn("text-sm text-muted-foreground", className)}
      {...props}
    />
  );
}

export {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
};
