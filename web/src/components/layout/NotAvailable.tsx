import { useTranslation } from "react-i18next";
import { MODULE_META, type ModuleId } from "@/modules";
import { useLayoutStore } from "@/stores/layout-store";

/**
 * Placeholder for a module whose view has not been built yet.
 *
 * It names what the module will do and says the rest of the app works, rather
 * than describing the state of the code.
 */
export function NotAvailable() {
  const { t } = useTranslation();
  const view = useLayoutStore((state) => state.view);
  const side = useLayoutStore((state) => state.side);
  const focused = useLayoutStore((state) => state.focused);
  const module: ModuleId = focused === "side" && side ? side : view;
  const meta = MODULE_META[module];

  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 p-8 text-center">
      <meta.icon className="size-8 text-muted-foreground/25" />
      <p className="text-sm font-medium">{t("placeholder.title")}</p>
      <p className="max-w-sm text-xs text-muted-foreground">{t("placeholder.body")}</p>
    </div>
  );
}
