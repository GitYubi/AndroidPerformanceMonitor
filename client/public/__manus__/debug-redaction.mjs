const sensitive = /password|token|secret|key|authorization|cookie|credential|parameters|session/i;

export function redactDebugField(key, value) {
  if (key !== "sessionEvents" && sensitive.test(key)) return "[REDACTED]";
  if (key.toLowerCase() === "headers" && value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).filter(([name]) =>
      /^(accept|content-type|content-length)$/i.test(name)
    ));
  }
  if (typeof value !== "string") return value;
  if (/^(url|href|location|referer|filename)$/i.test(key)) value = value.split(/[?#]/, 1)[0];
  return value
    .replace(/(https?:\/\/)[^/@\s]+@/gi, "$1")
    .replace(/((?:https?:\/\/|\/)[^\s"'?#]+)[?#][^\s"']*/g, "$1")
    .replace(/\b(Bearer|Basic)\s+[\w.~+/=-]+/gi, "$1 [REDACTED]")
    .replace(/(\b(?:password|token|secret|api[-_]?key|authorization|cookie|credential)\b["']?\s*[:=]\s*)(?:"[^"]*"|'[^']*'|[^ \t\r\n&,;]+)/gi, "$1[REDACTED]");
}
