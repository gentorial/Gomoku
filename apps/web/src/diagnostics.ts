import { configureDiagnostics, logDiagnostic, type DiagnosticRecord } from "@gomoku/engine-wasm";

// Vite's local collector persists browser/worker diagnostics without a game API.
export function initializeDiagnostics() {
  const records: DiagnosticRecord[] = [];
  let sending = false;
  let disabled = false;
  const endpoint = import.meta.env.BASE_URL + "__gomoku_logs";
  async function flush() {
    if (sending || disabled || records.length === 0) return;
    sending = true;
    const batch = records.splice(0, 64);
    try {
      const response = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(batch),
        signal: AbortSignal.timeout(5000),
      });
      if (!response.ok) throw new Error("Log collector returned " + response.status);
    } catch {
      // Avoid repeated background requests if the local collector is unavailable.
      disabled = true;
      console.warn("日志落盘不可用，后续日志仍输出到浏览器控制台");
    } finally {
      sending = false;
      if (!disabled && records.length >= 64) void flush();
    }
  }
  configureDiagnostics({
    level: __GOMOKU_LOG_LEVEL__,
    sink: import.meta.env.DEV
      ? (record) => {
          if (disabled) return;
          if (records.length >= 1000) {
            const debug = records.findIndex((entry) => entry.level === "debug");
            records.splice(debug >= 0 ? debug : 0, 1);
          }
          records.push(record);
          if (records.length >= 64) void flush();
        }
      : undefined,
  });
  if (import.meta.env.DEV && __GOMOKU_LOG_LEVEL__ !== "off") {
    const timer = setInterval(() => void flush(), 1000);
    const onHide = () => {
      if (document.visibilityState === "hidden" && !disabled && records.length) {
        navigator.sendBeacon(
          endpoint,
          new Blob([JSON.stringify(records.splice(0, 64))], { type: "application/json" }),
        );
      }
    };
    document.addEventListener("visibilitychange", onHide);
    import.meta.hot?.dispose(() => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", onHide);
      void flush();
    });
  }
  logDiagnostic("gui", "gui.started", { logLevel: __GOMOKU_LOG_LEVEL__ });
}
