export type LogLevel = "off" | "info" | "debug";
export type DiagnosticRecord = {
  timestampMs: number;
  level: "info" | "debug" | "warn" | "error";
  component: string;
  event: string;
  [key: string]: unknown;
};
let level: LogLevel = "info";
let sink: ((record: DiagnosticRecord) => void) | undefined;
let consoleOutput = true;

export function configureDiagnostics(options: {
  level?: LogLevel;
  sink?: (record: DiagnosticRecord) => void;
  console?: boolean;
}) {
  level = options.level ?? "info";
  sink = options.sink;
  consoleOutput = options.console ?? true;
}
export function diagnosticLevel() {
  return level;
}
export function emitDiagnostic(record: DiagnosticRecord) {
  if (level === "off" || (record.level === "debug" && level !== "debug")) return;
  try {
    const output =
      record.level === "error"
        ? console.error
        : record.level === "warn"
          ? console.warn
          : record.level === "debug"
            ? console.debug
            : console.info;
    if (consoleOutput) output.call(console, JSON.stringify(record));
    sink?.(record);
  } catch {
    /* Diagnostics must never interrupt a game. */
  }
}
export function logDiagnostic(
  component: string,
  event: string,
  fields: Record<string, unknown> = {},
  severity: DiagnosticRecord["level"] = "info",
) {
  emitDiagnostic({ ...fields, timestampMs: Date.now(), level: severity, component, event });
}
