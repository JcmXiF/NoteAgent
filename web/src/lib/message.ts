/**
 * A translatable label, expressed as data.
 *
 * Domain helpers must not depend on the i18n runtime: doing so would make them
 * awkward to unit test and would tie pure logic to React. Instead they return a
 * `Message`, and the component translates it:
 *
 * ```ts
 * const hint = failedCaptureHint(capture, hasDesktop);
 * return <p>{t(hint.key, hint.params)}</p>;
 * ```
 */
export type Message = {
  key: string;
  params?: Record<string, unknown>;
};

export function message(key: string, params?: Record<string, unknown>): Message {
  return params ? { key, params } : { key };
}
