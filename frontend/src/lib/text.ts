/** Sentence-case a UI label. Underscores become spaces; only the first letter is forced. */
export function sentenceCase(value: string | null | undefined): string {
  if (!value) return "";
  const normalized = value.replaceAll("_", " ");
  const match = normalized.match(/^(\s*)(\S)([\s\S]*)$/);
  if (!match) return normalized;
  return match[1] + match[2].toUpperCase() + match[3];
}

// Client-side display heuristic only, not a security boundary — the backend
// (recorder.py's redact()) is what actually decides what's sensitive before
// an artifact is ever sent here. This just keeps an obviously-named secret
// field masked in the console UI even if it slipped through unredacted.
export function isSensitiveKey(key: string): boolean {
  return /password|secret|token|ssn|pin/i.test(key);
}

export function maskSecret(value: unknown): string {
  const s = typeof value === "string" ? value : JSON.stringify(value ?? "");
  if (!s) return "••••";
  // Length is deliberately NOT the real secret's length (that alone leaks
  // information) — clamped to a fixed 4-10 range so the dot count doesn't
  // usefully distinguish a 6-char PIN from a 40-char token.
  return "•".repeat(Math.min(10, Math.max(4, s.length)));
}
