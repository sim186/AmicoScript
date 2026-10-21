import { appendJobLogLine, clientLog, resetJobLogs } from "./consoleLog.svelte";
import { switchTab, session } from "./session.svelte";
import { transcribeOptions } from "./transcribeOptions.svelte";
import { hydrateFromJob, resetTranscript } from "./transcript.svelte";

export const ALLOWED_EXTENSIONS = ["mp3", "wav", "m4a", "ogg", "flac", "mp4", "mov", "mkv", "opus"];

export interface BatchItem {
  file: File;
  jobId: string | null;
  recordingId: string | null;
  status: "pending" | "uploading" | "queued" | "processing" | "done" | "error";
  progress: number;
  message: string;
}

export const jobState = $state({
  selectedFile: null as File | null,
  processing: false,

  jobId: null as string | null,
  recordingId: null as string | null,
  jobStatus: null as string | null,
  jobProgress: 0,
  jobMessage: "",
  filename: "",

  batchMode: false,
  batchQueue: [] as BatchItem[],

  liveSegments: [] as { id: number; start: number; end: number; text: string }[],

  errorMessage: "",
});

let eventSource: EventSource | null = null;
// Keyed by jobId, not a single shared counter — otherwise one job's exhausted
// retry budget would leave an unrelated later job unable to reconnect at all.
const reconnectAttemptsByJob = new Map<string, number>();
let logPollTimer: ReturnType<typeof setInterval> | undefined;

export function showError(msg: string): void {
  jobState.errorMessage = msg;
}

export function hideError(): void {
  jobState.errorMessage = "";
}

function fmtBytes(b: number): string {
  return b < 1048576 ? `${(b / 1024).toFixed(0)} KB` : `${(b / 1048576).toFixed(1)} MB`;
}

export function isLikelyUrl(value: string): boolean {
  if (!value) return false;
  try {
    const u = new URL(value.trim());
    return u.protocol === "http:" || u.protocol === "https:";
  } catch {
    return false;
  }
}

export function clearFile(): void {
  jobState.selectedFile = null;
  if (jobState.batchMode) {
    jobState.batchMode = false;
    jobState.batchQueue = [];
  }
  hideError();
}

export function handleFile(file: File): void {
  if (jobState.batchMode) {
    handleFiles([file]);
    return;
  }
  if (jobState.selectedFile) {
    const prev = jobState.selectedFile;
    jobState.selectedFile = null;
    handleFiles([prev, file]);
    return;
  }
  const ext = file.name.split(".").pop()?.toLowerCase() || "";
  if (!ALLOWED_EXTENSIONS.includes(ext)) {
    clientLog(`Unsupported file: ${file.name}`, "WARN");
    showError(`Unsupported file type: .${ext}`);
    return;
  }
  clientLog(`File selected: ${file.name} (${fmtBytes(file.size)})`);
  hideError();
  jobState.selectedFile = file;
}

export function handleFiles(files: File[]): void {
  const valid = files.filter((f) => ALLOWED_EXTENSIONS.includes(f.name.split(".").pop()?.toLowerCase() || ""));
  const skipped = files.length - valid.length;
  if (valid.length === 0) {
    clientLog("Batch: no supported files found", "WARN");
    showError("No supported files found.");
    return;
  }
  clientLog(`Batch: ${valid.length} file(s) queued${skipped ? `, ${skipped} skipped` : ""}`);
  hideError();
  if (skipped > 0) showError(`${skipped} file(s) skipped — unsupported format.`);

  jobState.batchMode = true;
  jobState.selectedFile = null;
  valid.forEach((file) => {
    const dup = jobState.batchQueue.some((i) => i.file.name === file.name && i.file.size === file.size);
    if (!dup) {
      jobState.batchQueue.push({ file, jobId: null, recordingId: null, status: "pending", progress: 0, message: "" });
    }
  });
}

export function removeBatchItem(idx: number): void {
  jobState.batchQueue.splice(idx, 1);
  if (jobState.batchQueue.length === 0) jobState.batchMode = false;
}

export function clearPendingBatch(): void {
  jobState.batchQueue = jobState.batchQueue.filter((i) => i.status !== "pending");
  if (jobState.batchQueue.length === 0) jobState.batchMode = false;
}

export function newBatch(): void {
  jobState.batchQueue = [];
  jobState.batchMode = false;
  clearFile();
}

function buildTranscriptionFormData({ file, url }: { file?: File; url?: string }): FormData {
  const fd = new FormData();
  if (file) {
    fd.append("file", file);
  } else {
    fd.append("source_url", url || "");
    fd.append("allow_playlist", "true");
  }
  fd.append("model", transcribeOptions.selectedModel);
  fd.append("language", transcribeOptions.selectedLanguage);
  fd.append("diarize", transcribeOptions.diarize ? "true" : "false");
  if (transcribeOptions.cloudPower) fd.append("colab_url", transcribeOptions.colabUrl);
  fd.append("hf_token", transcribeOptions.hfToken);
  fd.append("num_speakers", transcribeOptions.numSpeakers);
  fd.append("min_speakers", transcribeOptions.minSpeakers);
  fd.append("max_speakers", transcribeOptions.maxSpeakers);
  return fd;
}

function uploadWithProgress(fd: FormData, onProgress: (pct: number) => void): Promise<any> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/transcribe");
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress(e.loaded / e.total);
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText));
        } catch {
          reject(new Error("Invalid server response"));
        }
      } else {
        try {
          reject(new Error(JSON.parse(xhr.responseText).detail || xhr.statusText));
        } catch {
          reject(new Error(xhr.statusText || `HTTP ${xhr.status}`));
        }
      }
    };
    xhr.onerror = () => reject(new Error("Network error — check your connection"));
    xhr.send(fd);
  });
}

export function setProcessing(active: boolean): void {
  jobState.processing = active;
  if (active) {
    session.transcriptEnabled = true;
    jobState.liveSegments = [];
    resetJobLogs();
    startLogPolling();
  } else {
    stopLogPolling();
  }
}

export async function loadResult(jobId: string, recordingId?: string | null): Promise<void> {
  jobState.jobId = jobId;
  if (recordingId) jobState.recordingId = recordingId;
  session.transcriptEnabled = true;
  setProcessing(false);
  switchTab("transcript");
  await hydrateFromJob(jobId, recordingId ?? jobState.recordingId);
}

/** Resets everything the Transcribe/Transcript flow tracks, for "+ New transcription". */
export function resetForNewTranscription(): void {
  eventSource?.close();
  eventSource = null;
  jobState.jobId = null;
  jobState.recordingId = null;
  jobState.jobStatus = null;
  jobState.jobProgress = 0;
  jobState.jobMessage = "";
  jobState.selectedFile = null;
  jobState.batchMode = false;
  jobState.batchQueue = [];
  jobState.liveSegments = [];
  session.transcriptEnabled = false;
  resetTranscript();
  resetJobLogs();
}

export function connectSSE(jobId: string): void {
  const es = new EventSource(`/api/jobs/${jobId}/stream`);
  eventSource = es;

  es.onmessage = (e) => {
    reconnectAttemptsByJob.delete(jobId);
    let data: any;
    try {
      data = JSON.parse(e.data);
    } catch {
      return;
    }
    if (data.heartbeat) return;

    jobState.jobStatus = data.status;
    jobState.jobProgress = data.progress;
    jobState.jobMessage = data.message || "";
    appendJobLogLine(`[${new Date().toLocaleTimeString()}] EVENT ${data.status}: ${jobState.jobMessage}`);

    if (data.status === "transcribing" && data.data?.segment) {
      jobState.liveSegments.push(data.data.segment);
    }

    if (data.status === "done") {
      es.close();
      reconnectAttemptsByJob.delete(jobId);
      refreshJobLogs();
      clientLog("Transcription complete");
      loadResult(jobId);
    } else if (data.status === "error") {
      es.close();
      reconnectAttemptsByJob.delete(jobId);
      refreshJobLogs();
      clientLog(`Transcription error: ${data.message}`, "ERROR");
      setProcessing(false);
      showError(`Transcription error: ${data.message}`);
    } else if (data.status === "cancelled") {
      es.close();
      reconnectAttemptsByJob.delete(jobId);
      refreshJobLogs();
      setProcessing(false);
    }
  };

  es.onerror = () => {
    const attempts = reconnectAttemptsByJob.get(jobId) || 0;
    if (attempts > 0 || ["done", "error", "cancelled"].includes(jobState.jobStatus || "")) return;
    reconnectAttemptsByJob.set(jobId, attempts + 1);
    es.close();
    setTimeout(() => connectSSE(jobId), 2000);
  };
}

export async function cancelJob(): Promise<void> {
  clientLog("Transcription cancelled by user", "WARN");
  eventSource?.close();
  if (jobState.jobId) await fetch(`/api/jobs/${jobState.jobId}/cancel`, { method: "POST" }).catch(() => {});
  setProcessing(false);
  jobState.jobId = null;
}

export async function startUrlTranscription(urlArg: string): Promise<void> {
  const urlValue = urlArg.trim();
  if (!urlValue) {
    showError("Paste a video URL first.");
    return;
  }

  hideError();
  clearFile();
  setProcessing(true);
  resetTranscript();
  switchTab("transcript");
  jobState.filename = urlValue;
  jobState.jobMessage = "Queueing URL...";
  jobState.jobProgress = 0;

  clientLog(`Queueing online source: ${urlValue}`);

  try {
    const res = await fetch("/api/transcribe/url", {
      method: "POST",
      body: buildTranscriptionFormData({ url: urlValue }),
    });
    const payload = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(payload.detail || `HTTP ${res.status}`);

    const count = Number(payload.count || 0);
    if (!payload.first_job_id) throw new Error("No job returned by backend");

    jobState.jobId = payload.first_job_id;
    jobState.recordingId = payload.first_recording_id || null;

    if (count > 1) {
      clientLog(`Playlist import queued ${count} jobs. Showing progress for the first item.`);
    } else {
      clientLog(`Source queued — job ${payload.first_job_id}`);
    }

    connectSSE(payload.first_job_id);
  } catch (err) {
    setProcessing(false);
    showError(`URL import failed: ${(err as Error).message}`);
  }
}

export async function startTranscription(): Promise<void> {
  if (jobState.batchMode) {
    startBatchTranscription();
    return;
  }
  if (!jobState.selectedFile) return;

  const file = jobState.selectedFile;
  jobState.selectedFile = null;

  const dest = transcribeOptions.cloudPower ? `Colab (${transcribeOptions.colabUrl})` : "local";
  clientLog(
    `Uploading ${file.name} → ${dest} [model=${transcribeOptions.selectedModel}, lang=${
      transcribeOptions.selectedLanguage || "auto"
    }, diarize=${transcribeOptions.diarize}]`,
  );
  hideError();

  const fd = buildTranscriptionFormData({ file });

  setProcessing(true);
  resetTranscript();
  switchTab("transcript");
  jobState.filename = file.name;
  jobState.jobMessage = "Uploading…";

  let lastMilestone = -1;
  try {
    const data = await uploadWithProgress(fd, (pct) => {
      const p = Math.round(pct * 100);
      jobState.jobMessage = p < 100 ? `Uploading… ${p}%` : "Queued…";
      jobState.jobProgress = pct;
      const milestone = Math.floor(p / 25) * 25;
      if (milestone > lastMilestone) {
        clientLog(`Upload ${milestone}%`);
        lastMilestone = milestone;
      }
    });
    clientLog(`Upload complete — job ${data.job_id}`);
    jobState.jobProgress = 0;
    jobState.jobId = data.job_id;
    jobState.recordingId = data.recording_id || null;
    connectSSE(data.job_id);
  } catch (err) {
    setProcessing(false);
    showError(`Upload failed: ${(err as Error).message}`);
  }
}

async function startBatchTranscription(): Promise<void> {
  const pending = jobState.batchQueue.filter((i) => i.status === "pending");
  if (pending.length === 0) return;
  resetJobLogs();
  startLogPolling();
  await Promise.allSettled(pending.map((item) => submitBatchItem(item)));
}

async function submitBatchItem(item: BatchItem): Promise<void> {
  item.status = "uploading";
  item.progress = 0;
  item.message = "Uploading…";
  clientLog(`Uploading ${item.file.name}…`);
  try {
    const data = await uploadWithProgress(buildTranscriptionFormData({ file: item.file }), (pct) => {
      item.progress = pct * 0.05;
      item.message = `Uploading… ${Math.round(pct * 100)}%`;
    });
    clientLog(`Upload complete: ${item.file.name} — job ${data.job_id}`);
    item.jobId = data.job_id;
    item.recordingId = data.recording_id || null;
    item.status = "queued";
    item.progress = 0;
    item.message = "";
    connectBatchSSE(item);
  } catch (err) {
    item.status = "error";
    item.message = (err as Error).message;
    checkBatchComplete();
  }
}

function connectBatchSSE(item: BatchItem): void {
  const es = new EventSource(`/api/jobs/${item.jobId}/stream`);
  es.onmessage = (e) => {
    let data: any;
    try {
      data = JSON.parse(e.data);
    } catch {
      return;
    }
    if (data.heartbeat) return;

    if (data.status === "done") item.status = "done";
    else if (data.status === "error" || data.status === "cancelled") item.status = "error";
    else item.status = data.progress > 0 ? "processing" : "queued";
    item.progress = data.progress || 0;
    item.message = data.message || "";

    if (["done", "error", "cancelled"].includes(data.status)) {
      es.close();
      checkBatchComplete();
    }
  };
  let retried = false;
  es.onerror = () => {
    es.close();
    if (!retried && item.status !== "done" && item.status !== "error") {
      retried = true;
      setTimeout(() => connectBatchSSE(item), 2000);
    }
  };
}

function checkBatchComplete(): void {
  const terminal = ["done", "error"];
  if (!jobState.batchQueue.every((i) => terminal.includes(i.status))) return;
  stopLogPolling();
  session.transcriptEnabled = jobState.batchQueue.some((i) => i.status === "done") || session.transcriptEnabled;
}

function getActiveJobId(): string | null {
  if (jobState.batchMode) {
    const processing = jobState.batchQueue.find((i) => i.status === "processing");
    if (processing?.jobId) return processing.jobId;
    const submitted = jobState.batchQueue.filter((i) => i.jobId).slice(-1)[0];
    return submitted?.jobId || null;
  }
  return jobState.jobId;
}

function formatJobLogEntry(entry: { ts?: number; level?: string; message?: string }): string {
  const ts = entry?.ts ? new Date(entry.ts * 1000).toLocaleTimeString() : "--:--:--";
  return `[${ts}] ${entry?.level || "INFO"} ${entry?.message || ""}`;
}

export async function refreshJobLogs(): Promise<void> {
  if (jobState.batchMode && jobState.batchQueue.length > 0) {
    const jobItems = jobState.batchQueue.filter((i) => i.jobId);
    if (!jobItems.length) return;
    const rawLogs: any[] = [];
    await Promise.allSettled(
      jobItems.map(async (item) => {
        try {
          const res = await fetch(`/api/jobs/${item.jobId}/logs?limit=500`);
          if (!res.ok) return;
          const payload = await res.json();
          const name = item.file?.name || item.jobId;
          (payload.logs || []).forEach((e: any) => rawLogs.push({ ...e, _name: name }));
        } catch {
          /* best-effort diagnostics only */
        }
      }),
    );
    rawLogs.sort((a, b) => (a.ts || 0) - (b.ts || 0));
    const lines = rawLogs.map((e) => {
      const ts = e.ts ? new Date(e.ts * 1000).toLocaleTimeString() : "--:--:--";
      return `[${ts}] [${e._name}] ${e.level || "INFO"} ${e.message || ""}`;
    });
    replaceJobLogs(lines.length > 1000 ? lines.slice(-1000) : lines);
    return;
  }
  const jobId = getActiveJobId();
  if (!jobId) return;
  try {
    const res = await fetch(`/api/jobs/${jobId}/logs?limit=500`);
    if (!res.ok) return;
    const payload = await res.json();
    replaceJobLogs((payload.logs || []).map(formatJobLogEntry));
  } catch {
    /* best-effort diagnostics only */
  }
}

function replaceJobLogs(lines: string[]): void {
  resetJobLogs();
  lines.forEach((l) => appendJobLogLine(l));
}

function startLogPolling(): void {
  stopLogPolling();
  refreshJobLogs();
  logPollTimer = setInterval(refreshJobLogs, 2000);
}

function stopLogPolling(): void {
  if (logPollTimer) {
    clearInterval(logPollTimer);
    logPollTimer = undefined;
  }
}
