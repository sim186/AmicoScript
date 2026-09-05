import { clientLog } from "./consoleLog.svelte";

export const MODELS = [
  { id: "tiny", name: "Tiny", params: "~39M", note: "Fastest · lowest accuracy" },
  { id: "base", name: "Base", params: "~74M", note: "Fast" },
  { id: "small", name: "Small", params: "~244M", note: "Balanced" },
  { id: "medium", name: "Medium", params: "~769M", note: "High accuracy" },
  { id: "large-v2", name: "Large v2", params: "~1.5B", note: "Best accuracy" },
  { id: "large-v3", name: "Large v3", params: "~1.5B", note: "Best accuracy+" },
] as const;

export const transcribeOptions = $state({
  selectedModel: "small",
  selectedLanguage: "",
  diarize: false,
  cloudPower: false,
  colabUrl: "",
  hfToken: "",
  numSpeakers: "",
  minSpeakers: "",
  maxSpeakers: "",
  exitToken: "",
});

export function selectModel(id: string): void {
  clientLog(`Model selected: ${id}`);
  transcribeOptions.selectedModel = id;
  try {
    localStorage.setItem("selectedModel", id);
  } catch {
    /* ignore */
  }
  saveTranscriptionDefaults();
}

export function selectLanguage(lang: string): void {
  transcribeOptions.selectedLanguage = lang;
  clientLog(`Language set: ${lang || "auto-detect"}`);
  try {
    localStorage.setItem("selectedLanguage", lang);
  } catch {
    /* ignore */
  }
  saveTranscriptionDefaults();
}

export function setDiarize(value: boolean): void {
  transcribeOptions.diarize = value;
  clientLog(`Diarization ${value ? "enabled" : "disabled"}`);
  try {
    localStorage.setItem("diarize", String(value));
  } catch {
    /* ignore */
  }
  saveTranscriptionDefaults();
}

export function setCloudPower(value: boolean): void {
  transcribeOptions.cloudPower = value;
  try {
    localStorage.setItem("cloudPower", String(value));
  } catch {
    /* ignore */
  }
}

export function setColabUrl(url: string): void {
  transcribeOptions.colabUrl = url.trim();
  try {
    localStorage.setItem("colabUrl", transcribeOptions.colabUrl);
  } catch {
    /* ignore */
  }
}

export function saveTranscriptionDefaults(): void {
  const fd = new FormData();
  fd.append("model", transcribeOptions.selectedModel);
  fd.append("language", transcribeOptions.selectedLanguage);
  fd.append("diarize", String(transcribeOptions.diarize));
  fetch("/api/settings", { method: "POST", body: fd }).catch(() => {});
}

let hfSaveTimer: ReturnType<typeof setTimeout> | undefined;

export function saveHfTokenDebounced(token: string, onSaved: () => void): void {
  clearTimeout(hfSaveTimer);
  hfSaveTimer = setTimeout(async () => {
    try {
      const fd = new FormData();
      fd.append("hf_token", token);
      const res = await fetch("/api/settings", { method: "POST", body: fd });
      if (res.ok) onSaved();
    } catch {
      /* ignore save errors */
    }
  }, 600);
}

export async function ensureSessionToken(): Promise<string> {
  if (transcribeOptions.exitToken) return transcribeOptions.exitToken;
  try {
    const res = await fetch("/api/settings");
    if (res.ok) {
      const data = await res.json();
      if (data.exit_token) transcribeOptions.exitToken = data.exit_token;
    }
  } catch {
    /* ignore */
  }
  return transcribeOptions.exitToken || "";
}

export interface RestoredHfToken {
  preview: string;
  masked: boolean;
  meetingCaptureEnabled: boolean;
  autoSummarizeMeetings: boolean;
}

/** Restores localStorage-backed prefs, then reconciles against server defaults. */
export async function restoreTranscriptionSettings(): Promise<RestoredHfToken> {
  let m: string | null = null;
  let l: string | null = null;
  let diarizeStored: string | null = null;
  let cloudPowerStored: string | null = null;
  let colabUrlStored: string | null = null;
  try {
    m = localStorage.getItem("selectedModel");
    l = localStorage.getItem("selectedLanguage");
    diarizeStored = localStorage.getItem("diarize");
    cloudPowerStored = localStorage.getItem("cloudPower");
    colabUrlStored = localStorage.getItem("colabUrl");
  } catch {
    /* ignore */
  }

  if (m) transcribeOptions.selectedModel = m;
  if (l !== null) transcribeOptions.selectedLanguage = l;
  if (diarizeStored === "true") transcribeOptions.diarize = true;
  if (cloudPowerStored === "true") transcribeOptions.cloudPower = true;
  if (colabUrlStored) transcribeOptions.colabUrl = colabUrlStored;

  let tokenPreview = "";
  let tokenSet = false;
  let meetingCaptureEnabled = false;
  let autoSummarizeMeetings = false;
  try {
    const res = await fetch("/api/settings");
    if (res.ok) {
      const data = await res.json();
      tokenSet = !!data.hf_token_set;
      tokenPreview = tokenSet ? data.hf_token_preview || "••••••••" : "";
      if (data.exit_token) transcribeOptions.exitToken = data.exit_token;
      meetingCaptureEnabled = !!data.meeting_capture_enabled;
      autoSummarizeMeetings = !!data.auto_summarize_meetings;
      if (!m && data.default_model) transcribeOptions.selectedModel = data.default_model;
      if (l === null && data.default_language) transcribeOptions.selectedLanguage = data.default_language;
      if (!diarizeStored && data.default_diarize) transcribeOptions.diarize = true;
    }
  } catch {
    /* server not available */
  }

  // Push the resolved values back so the server (and therefore the meeting
  // watcher) matches what the sidebar shows — an existing user whose settings
  // live only in localStorage would otherwise have auto-captured meetings
  // transcribed with different options than their manual uploads.
  saveTranscriptionDefaults();

  return { preview: tokenPreview, masked: tokenSet, meetingCaptureEnabled, autoSummarizeMeetings };
}
