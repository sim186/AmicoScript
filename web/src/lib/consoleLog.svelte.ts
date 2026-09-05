export type LogLevel = "INFO" | "WARN" | "ERROR";

export const consoleState = $state({
  clientLogs: [] as string[],
  jobLogs: [] as string[],
  open: false,
});

export function clientLog(msg: string, level: LogLevel = "INFO"): void {
  const ts = new Date().toLocaleTimeString();
  consoleState.clientLogs.push(`[${ts}] ${level} ${msg}`);
  if (consoleState.clientLogs.length > 500) {
    consoleState.clientLogs = consoleState.clientLogs.slice(-500);
  }
}

export function resetJobLogs(): void {
  consoleState.jobLogs = [];
}

export function appendJobLogLine(line: string): void {
  if (!line) return;
  consoleState.jobLogs.push(line);
  if (consoleState.jobLogs.length > 500) {
    consoleState.jobLogs = consoleState.jobLogs.slice(-500);
  }
}

export function combinedLogLines(): string[] {
  const all: string[] = [];
  if (consoleState.clientLogs.length) all.push(...consoleState.clientLogs);
  if (consoleState.clientLogs.length && consoleState.jobLogs.length) all.push("──── Backend ────");
  if (consoleState.jobLogs.length) all.push(...consoleState.jobLogs);
  return all;
}
