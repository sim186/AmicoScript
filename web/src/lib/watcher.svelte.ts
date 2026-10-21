import { clientLog } from "./consoleLog.svelte";
import { ensureSessionToken } from "./transcribeOptions.svelte";
import { elapsedSeconds } from "./time";

export interface WatcherInstaller {
  url: string;
  name: string;
  hint: string;
}

export interface WatcherStatus {
  alive: boolean;
  recording: boolean;
  app: string;
  since: number | null;
  installed_version: string;
  current_version: string;
  update_available: boolean;
  unsupported: string;
  host_platform: string;
  host_can_install: boolean;
  installers: Record<string, WatcherInstaller>;
}

function browserPlatform(): string {
  let p = "";
  try {
    p = (navigator as any).userAgentData?.platform || navigator.platform || navigator.userAgent || "";
  } catch {
    return "";
  }
  if (/win/i.test(p)) return "windows";
  if (/mac|iphone|ipad/i.test(p)) return "macos";
  if (/linux|x11|cros/i.test(p)) return "linux";
  return "";
}

export const watcherState = $state({
  alive: false,
  recording: false,
  app: "",
  since: null as number | null,
  unsupported: "",
  installer: null as WatcherInstaller | null,
  canInstall: false,
  onboardBannerVisible: false,
  onboardStepsVisible: false,
  updateBannerVisible: false,
  currentVersion: "",
});

let watcherMiss = 0;
let watcherTimerHandle: ReturnType<typeof setInterval> | undefined;

export function recordingElapsedLabel(nowMs: number): string {
  if (watcherState.since == null) return "0:00";
  const totalSeconds = elapsedSeconds(watcherState.since * 1000, nowMs);
  const h = Math.floor(totalSeconds / 3600);
  const m = Math.floor((totalSeconds % 3600) / 60);
  const s = totalSeconds % 60;
  const pad = (n: number) => String(n).padStart(2, "0");
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

function applyWatcherUI(d: WatcherStatus): void {
  const wasRecording = watcherState.recording;
  watcherState.recording = !!d.recording;
  watcherState.app = d.app || "";
  watcherState.since = d.recording ? d.since : null;
  watcherState.unsupported = d.unsupported || "";

  const installer = d.installers?.[browserPlatform()] || null;
  watcherState.installer = installer;
  watcherState.canInstall = !!(d.host_can_install || installer);
  watcherState.alive = d.alive;
  watcherState.currentVersion = d.current_version;

  if (d.alive || !watcherState.canInstall) {
    if (d.alive) watcherMiss = 0;
    watcherState.onboardBannerVisible = false;
    watcherState.onboardStepsVisible = false;
  } else {
    watcherMiss++;
    let dismissed = false;
    try {
      dismissed = localStorage.getItem("watcherSetupDismissed") === "1";
    } catch {
      /* ignore */
    }
    if (!dismissed && watcherMiss >= 2) watcherState.onboardBannerVisible = true;
  }

  if (d.update_available) {
    let dismissedVersion = "";
    try {
      dismissedVersion = localStorage.getItem("watcherUpdateDismissedFor") || "";
    } catch {
      /* ignore */
    }
    watcherState.updateBannerVisible = dismissedVersion !== d.current_version;
  } else {
    watcherState.updateBannerVisible = false;
  }
}

export async function refreshRecordingChip(): Promise<void> {
  try {
    const res = await fetch("/api/watcher/status");
    if (!res.ok) return;
    applyWatcherUI(await res.json());
  } catch {
    /* server momentarily unreachable — leave UI as-is */
  }
}

export function dismissWatcherOnboarding(): void {
  try {
    localStorage.setItem("watcherSetupDismissed", "1");
  } catch {
    /* ignore */
  }
  watcherState.onboardBannerVisible = false;
  watcherState.onboardStepsVisible = false;
}

export function revealWatcherOnboardingSteps(): void {
  watcherState.onboardStepsVisible = true;
}

export function dismissWatcherUpdateBanner(): void {
  try {
    localStorage.setItem("watcherUpdateDismissedFor", watcherState.currentVersion || "");
  } catch {
    /* ignore */
  }
  watcherState.updateBannerVisible = false;
}

export async function updateWatcherNow(): Promise<{ ok: boolean; error?: string }> {
  try {
    const token = await ensureSessionToken();
    const fd = new FormData();
    fd.append("token", token);
    const res = await fetch("/api/watcher/install", { method: "POST", body: fd });
    const data = res.ok ? await res.json() : { ok: false };
    if (data.ok) {
      setTimeout(refreshRecordingChip, 2000);
      return { ok: true };
    }
    watcherState.onboardBannerVisible = true;
    return {
      ok: false,
      error:
        data.error === "unsupported_host"
          ? "Can't auto-update from here — download and run the setup script on this computer instead."
          : `Auto-update failed (${data.error || "unknown error"}) — try the setup script instead.`,
    };
  } catch (err) {
    return { ok: false, error: (err as Error).message };
  }
}

export function setMeetingCaptureToggleState(on: boolean): void {
  meetingCaptureState.enabled = on;
}

export const meetingCaptureState = $state({ enabled: false });

export async function toggleMeetingCapture(): Promise<void> {
  const next = !meetingCaptureState.enabled;
  try {
    const token = await ensureSessionToken();
    meetingCaptureState.enabled = next;
    const fd = new FormData();
    fd.append("enabled", String(next));
    fd.append("token", token);
    const res = await fetch("/api/settings/meeting-capture", { method: "POST", body: fd });
    if (!res.ok) throw new Error("save failed");
    const data = await res.json().catch(() => ({}));
    if (next && data.installed) clientLog("Watcher auto-installed — starting…");
    else if (next && data.started) clientLog("Watcher starting…");
    setTimeout(refreshRecordingChip, 1500);
    setTimeout(refreshRecordingChip, 4000);
  } catch {
    meetingCaptureState.enabled = !next;
  }
}

export function startWatcherPolling(): void {
  if (watcherTimerHandle) return;
  refreshRecordingChip();
  watcherTimerHandle = setInterval(refreshRecordingChip, 1000);
}

export const autoSummaryState = $state({ enabled: false, llmConfigured: false });

export async function toggleAutoSummary(): Promise<void> {
  const next = !autoSummaryState.enabled;
  autoSummaryState.enabled = next;
  const fd = new FormData();
  fd.append("auto_summarize_meetings", String(next));
  try {
    const res = await fetch("/api/settings", { method: "POST", body: fd });
    if (!res.ok) throw new Error(String(res.status));
  } catch {
    autoSummaryState.enabled = !next;
  }
}
