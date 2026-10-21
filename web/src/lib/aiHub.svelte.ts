import { marked } from "marked";
import { clientLog } from "./consoleLog.svelte";
import { transcriptState } from "./transcript.svelte";

export type AnalysisType = "summary" | "action_items" | "translate" | "custom";

export interface AiMessage {
  id: string;
  role: "user" | "assistant";
  html: string;
  spinner?: string;
}

export const aiHubState = $state({
  open: false,
  analyzeType: "summary" as AnalysisType,
  targetLanguage: "",
  customPrompt: "",
  outputLanguage: "",
  jobId: null as string | null,
  messages: [] as AiMessage[],
  pastCount: 0,
});

function escHtml(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

export function toggleHub(): void {
  aiHubState.open = !aiHubState.open;
  if (aiHubState.open) clientLog("AI Assistant opened");
}

export function closeHub(): void {
  aiHubState.open = false;
}

export function setAnalyzeType(type: AnalysisType): void {
  aiHubState.analyzeType = type;
}

let rawAiText = "";
let es: EventSource | null = null;

function addMessage(role: "user" | "assistant", html: string, id?: string, spinner?: string): string {
  const msgId = id || `${role}-${Date.now()}`;
  aiHubState.messages.push({ id: msgId, role, html, spinner });
  return msgId;
}

function updateMessage(id: string, html: string, spinner?: string): void {
  const msg = aiHubState.messages.find((m) => m.id === id);
  if (msg) {
    msg.html = html;
    msg.spinner = spinner;
  }
}

export async function runAnalysis(): Promise<void> {
  const recId = transcriptState.recordingId;
  if (!recId) {
    addMessage("assistant", "No recording loaded. Open a recording first.");
    return;
  }
  const type = aiHubState.analyzeType;
  const label = type === "custom" ? aiHubState.customPrompt.trim() || "Custom analysis" : type.replace("_", " ");
  addMessage("user", escHtml(label));

  const msgId = addMessage("assistant", "", undefined, "Analyzing…");

  const fd = new FormData();
  fd.append("analysis_type", type);
  if (type === "translate") fd.append("target_language", aiHubState.targetLanguage.trim() || "English");
  if (type === "custom") {
    const prompt = aiHubState.customPrompt.trim();
    if (!prompt) {
      updateMessage(msgId, "Enter a custom prompt first.");
      return;
    }
    fd.append("custom_prompt", prompt);
  }
  if (aiHubState.outputLanguage.trim()) fd.append("output_language", aiHubState.outputLanguage.trim());

  try {
    const res = await fetch(`/api/recordings/${recId}/analyses`, { method: "POST", body: fd });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || "Failed");
    const { job_id } = await res.json();
    connectAnalysisSSE(job_id, msgId, recId);
  } catch (err) {
    updateMessage(msgId, `<span class="text-red-500">Error: ${escHtml((err as Error).message)}</span>`);
  }
}

function connectAnalysisSSE(jobId: string, msgId: string, recId: string): void {
  aiHubState.jobId = jobId;
  rawAiText = "";
  es = new EventSource(`/api/jobs/${jobId}/stream`);
  es.onmessage = (e) => {
    let data: any;
    try {
      data = JSON.parse(e.data);
    } catch {
      return;
    }
    if (data.heartbeat) return;
    if (data.status === "streaming" && data.data?.chunk) {
      rawAiText += data.data.chunk;
      updateMessage(msgId, escHtml(rawAiText).replace(/\n/g, "<br>"), "Generating…");
    } else if (data.status === "done") {
      es?.close();
      es = null;
      aiHubState.jobId = null;
      updateMessage(msgId, marked.parse(rawAiText) as string);
      clientLog("AI analysis complete");
      loadPastCount(recId);
    } else if (data.status === "error") {
      es?.close();
      es = null;
      aiHubState.jobId = null;
      updateMessage(msgId, `<span class="text-red-500">Error: ${escHtml(data.message || "unknown")}</span>`);
    } else if (data.status === "cancelled") {
      es?.close();
      es = null;
      aiHubState.jobId = null;
      updateMessage(msgId, rawAiText ? (marked.parse(rawAiText) as string) : "<em>Cancelled</em>");
    }
  };
  es.onerror = () => {
    es?.close();
    es = null;
    aiHubState.jobId = null;
    updateMessage(msgId, rawAiText ? (marked.parse(rawAiText) as string) : "<em>Connection lost</em>");
  };
}

export function stopAnalysis(): void {
  if (es) {
    es.close();
    es = null;
  }
  if (aiHubState.jobId) {
    fetch(`/api/jobs/${aiHubState.jobId}/cancel`, { method: "POST" }).catch(() => {});
    aiHubState.jobId = null;
  }
}

async function loadPastCount(recordingId: string): Promise<void> {
  try {
    const res = await fetch(`/api/recordings/${recordingId}/analyses`);
    if (!res.ok) return;
    const analyses = await res.json();
    aiHubState.pastCount = analyses.length;
  } catch {
    /* ignore */
  }
}
