export function redactSecret(value: string, secret: string) {
  if (!secret) return value;
  return [secret, encodeURIComponent(secret)].reduce(
    (current, candidate) => current.split(candidate).join('[REDACTED]'),
    value,
  );
}

export function assertArtifactContainsNoSecret(value: unknown, secret: string) {
  const serialized = JSON.stringify(value);
  if (secret && (serialized.includes(secret) || serialized.includes(encodeURIComponent(secret)))) {
    throw new Error('Refusing to write an artifact containing the test token');
  }
}
