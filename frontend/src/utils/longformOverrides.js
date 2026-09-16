export const DEFAULT_OVERRIDES = {
  numStep: null,
  guidanceScale: null,
  posTemp: null,
  classTemp: null,
  postprocess: null,
  seed: null,
  varyRepeats: false,
  emoText: '',
  emoAlpha: null,
};

/**
 * Lower the persisted overrides (+ language) into the snake_case request fields
 * the backend expects. Only NON-default values are emitted, so an untouched
 * panel adds nothing to the body → today's exact request. Shared by the full
 * render and the per-chapter preview so both hit the same cache slot.
 */
export function overridesToRequest(overrides, language) {
  const o = overrides || DEFAULT_OVERRIDES;
  const body = {};
  if (language && language !== 'Auto') body.language = language;
  if (o.postprocess != null) body.postprocess_output = o.postprocess;
  if (o.varyRepeats) body.vary_repeats = true;
  return body;
}
