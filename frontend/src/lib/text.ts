/** Sentence-case a UI label. Underscores become spaces; only the first letter is forced. */
export function sentenceCase(value: string | null | undefined): string {
  if (!value) return "";
  const normalized = value.replaceAll("_", " ");
  const match = normalized.match(/^(\s*)(\S)([\s\S]*)$/);
  if (!match) return normalized;
  return match[1] + match[2].toUpperCase() + match[3];
}

export function isSensitiveKey(key: string): boolean {
  return /password|secret|token|ssn|pin/i.test(key);
}

export function maskSecret(value: unknown): string {
  const s = typeof value === "string" ? value : JSON.stringify(value ?? "");
  if (!s) return "••••";
  return "•".repeat(Math.min(10, Math.max(4, s.length)));
}
