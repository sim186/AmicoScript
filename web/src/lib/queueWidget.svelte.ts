import { connectSSE, jobState, loadResult, setProcessing } from "./jobs.svelte";
import { switchTab } from "./session.svelte";

export interface QueueJob {
  id: string;
  status: string;
  progress: number;
  position?: number;
  filename?: string;
  source_url?: string;
}

const QUEUE_STATUS_LABEL: Record<string, string> = {
  queued: "Queued",
  downloading: "Downloading",
  preparing: "Preparing",
  loading_model: "Loading model",
  transcribing: "Transcribing",
  diarizing: "Diarizing",
  translating: "Translating",
  running: "Analyzing",
  streaming: "Analyzing",
  warning: "Warning",
};

export function queueStatusLabel(status: string): string {
  return QUEUE_STATUS_LABEL[status] || status;
}

export const queueWidget = $state({
  jobs: [] as QueueJob[],
  panelOpen: false,
});

export function toggleQueuePanel(force?: boolean): void {
  queueWidget.panelOpen = typeof force === "boolean" ? force : !queueWidget.panelOpen;
}

export function attachToJob(jobId: string): void {
  if (!jobId) return;
  if (jobState.jobId === jobId) {
    switchTab("transcript");
    toggleQueuePanel(false);
    return;
  }
  jobState.jobId = jobId;
  jobState.recordingId = null;
  setProcessing(true);
  connectSSE(jobId);
  switchTab("transcript");
  toggleQueuePanel(false);
}

export async function cancelQueuedJob(jobId: string): Promise<void> {
  queueWidget.jobs = queueWidget.jobs.filter((j) => j.id !== jobId);
  try {
    await fetch(`/api/jobs/${jobId}/cancel`, { method: "POST" });
  } catch {
    /* ignore */
  }
  await refreshQueueWidget();
}

export async function refreshQueueWidget(): Promise<void> {
  try {
    const res = await fetch("/api/jobs");
    if (!res.ok) return;
    const data = await res.json();
    queueWidget.jobs = data.jobs || [];
  } catch {
    /* server momentarily unreachable */
  }
}

let pollTimer: ReturnType<typeof setInterval> | undefined;

export function startQueuePolling(): void {
  if (pollTimer) return;
  refreshQueueWidget();
  pollTimer = setInterval(refreshQueueWidget, 2000);
}

export function loadResultForItem(jobId: string, recordingId: string | null): void {
  loadResult(jobId, recordingId);
}
