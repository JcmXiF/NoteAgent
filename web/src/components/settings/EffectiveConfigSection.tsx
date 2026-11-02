import { useTranslation } from "react-i18next";
import {
  FieldGroup,
  SettingsSection,
  SourceBadge,
} from "@/components/settings/fields";
import {
  valueSource,
  type InstallSettings,
  type ValueSource,
} from "@/lib/install-settings";

/**
 * What the running core will actually use, and why.
 *
 * Read-only on purpose: this is the answer to a disagreement, not another
 * place to make one. An environment value is shown as it is used, with its
 * source, instead of pretending the document has the last word.
 */

function Row({
  label,
  value,
  source,
  mono,
}: {
  label: string;
  value: string;
  source?: ValueSource;
  mono?: boolean;
}) {
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        {label}
        {source ? <SourceBadge source={source} /> : null}
      </dt>
      <dd className={mono ? "font-mono text-xs break-all" : "text-sm"}>{value}</dd>
    </div>
  );
}

export function EffectiveConfigSection({ install }: { install: InstallSettings }) {
  const { t } = useTranslation();
  const source = (field: string) => valueSource(install.sources, field);
  const active =
    install.profiles.find((profile) => profile.id === install.activeProfileId) ?? null;
  const unset = t("settings.effective.unset");
  const text = (value: string) => value || unset;
  const keyState = (stored: boolean) =>
    stored ? t("settings.effective.stored") : unset;
  const provider =
    install.search.provider === "none"
      ? t("settings.effective.providerNone")
      : t(`settings.search.provider_${install.search.provider}`);

  return (
    <SettingsSection
      title={t("settings.effective.title")}
      intro={t("settings.effective.intro")}
    >
      <FieldGroup title={t("settings.effective.groupConnection")}>
        <dl className="flex flex-col gap-3">
          <Row
            label={t("settings.effective.profile")}
            value={active?.name ?? t("settings.effective.noConnection")}
          />
          <Row
            label={t("settings.profiles.baseUrl")}
            value={text(install.llm.baseUrl)}
            source={source("llm.baseUrl")}
            mono
          />
          <Row
            label={t("settings.profiles.model")}
            value={text(install.llm.model)}
            source={source("llm.model")}
          />
          <Row
            label={t("settings.profiles.apiKey")}
            value={keyState(install.llm.apiKeySet)}
            source={source("llm.apiKey")}
          />
        </dl>
      </FieldGroup>

      <FieldGroup title={t("settings.model.groupGeneration")}>
        <dl className="flex flex-col gap-3">
          <Row
            label={t("settings.model.temperature")}
            value={String(install.llm.temperature)}
            source={source("llm.temperature")}
          />
          <Row
            label={t("settings.model.maxTokens")}
            value={String(install.llm.maxTokens)}
            source={source("llm.maxTokens")}
          />
          <Row
            label={t("settings.model.timeout")}
            value={String(install.llm.timeoutSeconds)}
            source={source("llm.timeoutSeconds")}
          />
        </dl>
      </FieldGroup>

      <FieldGroup title={t("settings.effective.groupSearch")}>
        <dl className="flex flex-col gap-3">
          <Row
            label={t("settings.search.provider")}
            value={provider}
            source={source("search.provider")}
          />
          <Row
            label={t("settings.search.apiKey")}
            value={keyState(install.search.apiKeySet)}
            source={source("search.apiKey")}
          />
          <Row
            label={t("settings.search.timeout")}
            value={String(install.search.timeoutSeconds)}
            source={source("search.timeoutSeconds")}
          />
          <Row
            label={t("settings.search.country")}
            value={text(install.search.country)}
            source={source("search.country")}
          />
          <Row
            label={t("settings.search.lang")}
            value={text(install.search.searchLang)}
            source={source("search.searchLang")}
          />
          <Row
            label={t("settings.search.uiLang")}
            value={text(install.search.uiLang)}
            source={source("search.uiLang")}
          />
        </dl>
      </FieldGroup>

      <FieldGroup title={t("settings.effective.groupFile")}>
        <dl className="flex flex-col gap-3">
          <Row
            label={t("settings.search.extractMedia")}
            value={
              install.extractMedia
                ? t("settings.effective.on")
                : t("settings.effective.off")
            }
            source={source("extractMedia")}
          />
        </dl>
      </FieldGroup>
    </SettingsSection>
  );
}
