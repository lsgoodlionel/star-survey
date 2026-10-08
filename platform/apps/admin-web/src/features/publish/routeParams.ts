const canonicalUuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const canonicalPositiveInteger = /^[1-9][0-9]*$/;

export function parseSurveyIdParam(value: string | undefined) {
  return value && canonicalUuid.test(value) ? value : null;
}

export function parsePositiveIntegerParam(value: string | undefined) {
  if (!value || !canonicalPositiveInteger.test(value)) return null;
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) ? parsed : null;
}
