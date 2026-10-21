import { clientLog } from "./consoleLog.svelte";
import { throwIfFailed } from "./apiError";
import type { Recording } from "./library.svelte";

export interface TranscriptSegment {
  id: number;
  start: number;
  end: number;
  text: string;
  speaker?: string | null;
  translation?: string;
  edited?: boolean;
  original_text?: string;
}

export interface TranscriptResult {
  language?: string;
  duration: number;
  num_segments: number;
  speakers: string[];
  segments: TranscriptSegment[];
  filename?: string;
}

export const SPEAKER_COLORS = ["#6c63ff", "#f59e0b", "#10b981", "#f472b6", "#60a5fa", "#fb7185", "#a78bfa", "#fbbf24"];
export const SPEAKER_TEXT_COLORS = ["#6c63ff", "#d97706", "#059669", "#db2777", "#2563eb", "#e11d48", "#7c3aed", "#d97706"];

export const transcriptState = $state({
  recordingId: null as string | null,
  currentRecording: null as Recording | null,
  result: null as TranscriptResult | null,
  audioUrl: null as string | null,
  filteredSegments: [] as TranscriptSegment[],
  searchQuery: "",
  activeSpeakerFilter: null as string | null,
  currentSegmentId: null as number | null,
  selectedSegmentIds: new Set<number>(),
  lastSelectedSegmentId: null as number | null,
  translateAllJobId: null as string | null,
  translateAllProgress: 0,
  errorMessage: "",
});

export function speakerColor(speaker: string | null | undefined): string {
  const idx = speaker && transcriptState.result ? transcriptState.result.speakers.indexOf(speaker) : -1;
  return SPEAKER_COLORS[(idx < 0 ? 0 : idx) % 8];
}

export function speakerTextColor(speaker: string | null | undefined): string {
  const idx = speaker && transcriptState.result ? transcriptState.result.speakers.indexOf(speaker) : -1;
  return SPEAKER_TEXT_COLORS[(idx < 0 ? 0 : idx) % 8];
}

export function fmtDur(seconds: number): string {
  const t = Math.floor(seconds || 0);
  return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, "0")}`;
}

function showError(msg: string): void {
  transcriptState.errorMessage = msg;
}

function resetOpenState(): void {
  transcriptState.activeSpeakerFilter = null;
  transcriptState.currentSegmentId = null;
  transcriptState.searchQuery = "";
  transcriptState.selectedSegmentIds = new Set();
  transcriptState.lastSelectedSegmentId = null;
  transcriptState.errorMessage = "";
}

export function applyFilters(): void {
  const result = transcriptState.result;
  if (!result) {
    transcriptState.filteredSegments = [];
    return;
  }
  let segs = result.segments;
  if (transcriptState.activeSpeakerFilter) segs = segs.filter((s) => s.speaker === transcriptState.activeSpeakerFilter);
  const q = transcriptState.searchQuery.toLowerCase().trim();
  if (q) {
    segs = segs.filter((s) => s.text.toLowerCase().includes(q) || (s.translation && s.translation.toLowerCase().includes(q)));
  }
  transcriptState.filteredSegments = segs;
}

/** Populate transcript state from a job that just finished (fresh transcription). */
export async function hydrateFromJob(jobId: string, recordingId: string | null): Promise<void> {
  try {
    const [resultRes, audioRes] = await Promise.all([fetch(`/api/jobs/${jobId}/result`), fetch(`/api/audio/${jobId}`)]);
    if (!resultRes.ok) throw new Error("Failed to fetch result");
    const result: TranscriptResult = await resultRes.json();
    const audioBlob = await audioRes.blob();

    transcriptState.recordingId = recordingId;
    transcriptState.result = result;
    transcriptState.audioUrl = URL.createObjectURL(audioBlob);
    transcriptState.filteredSegments = result.segments;
    resetOpenState();

    if (recordingId) {
      try {
        const recRes = await fetch(`/api/recordings/${recordingId}`);
        transcriptState.currentRecording = recRes.ok ? await recRes.json() : null;
      } catch {
        transcriptState.currentRecording = null;
      }
    }
    applyFilters();
  } catch (err) {
    showError(`Failed to load result: ${(err as Error).message}`);
  }
}

/** Open a recording from the library into the transcript view. */
export async function openRecording(rec: { id: string; status: string; filename: string }): Promise<boolean> {
  if (rec.status !== "done") {
    showError(`This recording has status "${rec.status}" and cannot be opened yet.`);
    return false;
  }
  clientLog(`Opening recording: ${rec.filename}`);
  try {
    const [trRes, audioRes] = await Promise.all([
      fetch(`/api/recordings/${rec.id}/transcript`),
      fetch(`/api/recordings/${rec.id}/audio`),
    ]);
    if (!trRes.ok) throw new Error("Transcript not found");
    const trData = await trRes.json();
    const audioBlob = await audioRes.blob();

    const result: TranscriptResult = trData.json_data;
    result.filename = rec.filename;

    transcriptState.recordingId = rec.id;
    transcriptState.result = result;
    transcriptState.audioUrl = URL.createObjectURL(audioBlob);
    transcriptState.filteredSegments = result.segments;
    resetOpenState();

    try {
      const recRes = await fetch(`/api/recordings/${rec.id}`);
      transcriptState.currentRecording = recRes.ok ? await recRes.json() : (rec as Recording);
    } catch {
      transcriptState.currentRecording = rec as Recording;
    }

    return true;
  } catch (err) {
    showError(`Failed to open recording: ${(err as Error).message}`);
    return false;
  }
}

export async function reloadCurrentRecording(): Promise<void> {
  const recId = transcriptState.recordingId;
  if (!recId) return;
  try {
    const res = await fetch(`/api/recordings/${recId}/transcript`);
    if (!res.ok) throw new Error("Transcript not found");
    const data = await res.json();
    transcriptState.result = data.json_data;
    applyFilters();
  } catch (err) {
    showError(`Failed to reload recording: ${(err as Error).message}`);
  }
}

export function resetTranscript(): void {
  transcriptState.recordingId = null;
  transcriptState.currentRecording = null;
  transcriptState.result = null;
  transcriptState.audioUrl = null;
  transcriptState.filteredSegments = [];
  resetOpenState();
}

export function setSearchQuery(q: string): void {
  transcriptState.searchQuery = q;
  applyFilters();
}

export function toggleSpeakerFilter(speaker: string): void {
  transcriptState.activeSpeakerFilter = transcriptState.activeSpeakerFilter === speaker ? null : speaker;
  applyFilters();
}

export function setCurrentSegment(id: number | null): void {
  transcriptState.currentSegmentId = id;
}

export function toggleSegmentSelection(segId: number, shiftKey = false): void {
  if (shiftKey && transcriptState.lastSelectedSegmentId !== null) {
    const ids = transcriptState.filteredSegments.map((s) => s.id);
    const lastIdx = ids.indexOf(transcriptState.lastSelectedSegmentId);
    const currIdx = ids.indexOf(segId);
    if (lastIdx !== -1 && currIdx !== -1) {
      const [start, end] = lastIdx < currIdx ? [lastIdx, currIdx] : [currIdx, lastIdx];
      for (let i = start; i <= end; i++) transcriptState.selectedSegmentIds.add(ids[i]);
    }
  } else if (transcriptState.selectedSegmentIds.has(segId)) {
    transcriptState.selectedSegmentIds.delete(segId);
  } else {
    transcriptState.selectedSegmentIds.add(segId);
  }
  transcriptState.lastSelectedSegmentId = segId;
  transcriptState.selectedSegmentIds = new Set(transcriptState.selectedSegmentIds);
}

export function selectAllVisible(checked: boolean): void {
  const next = new Set(transcriptState.selectedSegmentIds);
  transcriptState.filteredSegments.forEach((s) => (checked ? next.add(s.id) : next.delete(s.id)));
  transcriptState.selectedSegmentIds = next;
}

export function clearSegmentSelection(): void {
  transcriptState.selectedSegmentIds = new Set();
  transcriptState.lastSelectedSegmentId = null;
}

export async function renameSpeaker(oldName: string, newName: string): Promise<void> {
  if (!newName || newName === oldName || !transcriptState.recordingId || !transcriptState.result) return;
  try {
    const fd = new FormData();
    fd.append("old_name", oldName);
    fd.append("new_name", newName);
    await throwIfFailed(
      await fetch(`/api/recordings/${transcriptState.recordingId}/transcript/rename-speaker`, { method: "POST", body: fd }),
      "Rename failed.",
    );
    const idx = transcriptState.result.speakers.indexOf(oldName);
    if (idx !== -1) {
      transcriptState.result.speakers[idx] = newName;
      transcriptState.result.speakers.sort();
    }
    transcriptState.result.segments.forEach((seg) => {
      if (seg.speaker === oldName) seg.speaker = newName;
    });
    if (transcriptState.activeSpeakerFilter === oldName) transcriptState.activeSpeakerFilter = newName;
    applyFilters();
  } catch (err) {
    showError(`Rename failed: ${(err as Error).message}`);
  }
}

export async function assignSpeakerToSelected(speakerName: string): Promise<void> {
  const name = speakerName.trim();
  if (!name || transcriptState.selectedSegmentIds.size === 0 || !transcriptState.recordingId || !transcriptState.result) return;
  const indices = Array.from(transcriptState.selectedSegmentIds).join(",");
  const fd = new FormData();
  fd.append("segment_indices", indices);
  fd.append("speaker_name", name);
  try {
    const res = await throwIfFailed(
      await fetch(`/api/recordings/${transcriptState.recordingId}/transcript/assign-speaker`, { method: "POST", body: fd }),
      "Assign speaker failed.",
    );
    const data = await res.json();
    const result = transcriptState.result;
    transcriptState.selectedSegmentIds.forEach((segId) => {
      const seg = result.segments.find((s) => s.id === segId);
      if (seg) seg.speaker = name;
    });
    if (data.speakers) result.speakers = data.speakers;
    clearSegmentSelection();
    applyFilters();
  } catch (err) {
    showError(`Assign speaker failed: ${(err as Error).message}`);
  }
}

export async function saveSegmentEdit(segId: number, text: string): Promise<void> {
  const newText = text.trim();
  if (!newText || !transcriptState.recordingId || !transcriptState.result) return;
  const fd = new FormData();
  fd.append("text", newText);
  try {
    await throwIfFailed(
      await fetch(`/api/recordings/${transcriptState.recordingId}/transcript/segments/${segId}`, { method: "PATCH", body: fd }),
      "Failed to save edit.",
    );
    const seg = transcriptState.result.segments.find((s) => s.id === segId);
    if (seg) {
      if (!seg.original_text) seg.original_text = seg.text;
      seg.text = newText;
      seg.edited = true;
    }
    applyFilters();
  } catch (err) {
    showError((err as Error).message);
  }
}

export async function resetSegment(segId: number): Promise<void> {
  if (!transcriptState.recordingId || !transcriptState.result) return;
  try {
    const res = await throwIfFailed(
      await fetch(`/api/recordings/${transcriptState.recordingId}/transcript/segments/${segId}/reset`, { method: "POST" }),
      "Failed to reset.",
    );
    const data = await res.json();
    const seg = transcriptState.result.segments.find((s) => s.id === segId);
    if (seg) {
      seg.text = data.text;
      seg.edited = false;
    }
    applyFilters();
  } catch (err) {
    showError((err as Error).message);
  }
}

export async function translateSegment(segId: number): Promise<void> {
  if (!transcriptState.recordingId || !transcriptState.result) return;
  try {
    const res = await throwIfFailed(
      await fetch(`/api/recordings/${transcriptState.recordingId}/transcript/segments/${segId}/translate`, { method: "POST" }),
      "Translation failed.",
    );
    const data = await res.json();
    const seg = transcriptState.result.segments.find((s) => s.id === segId);
    if (seg) seg.translation = data.translation;
    applyFilters();
  } catch (err) {
    showError((err as Error).message);
  }
}

export async function translateAll(): Promise<void> {
  const recId = transcriptState.recordingId;
  if (!recId) return;

  if (transcriptState.translateAllJobId) {
    if (!window.confirm("Stop the translation job?")) return;
    await fetch(`/api/jobs/${transcriptState.translateAllJobId}/cancel`, { method: "POST" }).catch(() => {});
    transcriptState.translateAllJobId = null;
    return;
  }

  if (!window.confirm("Translate all segments to English? This may take a while.")) return;

  try {
    const res = await throwIfFailed(
      await fetch(`/api/recordings/${recId}/transcript/translate-all`, { method: "POST" }),
      "Bulk translation failed.",
    );
    const { job_id } = await res.json();
    transcriptState.translateAllJobId = job_id;
    transcriptState.translateAllProgress = 0;

    const es = new EventSource(`/api/jobs/${job_id}/stream`);
    es.onmessage = (e) => {
      let data: any;
      try {
        data = JSON.parse(e.data);
      } catch {
        return;
      }
      if (data.heartbeat) return;
      if (data.status === "translating") {
        transcriptState.translateAllProgress = data.progress || 0;
      } else if (data.status === "done") {
        es.close();
        transcriptState.translateAllJobId = null;
        if (recId === transcriptState.recordingId) reloadCurrentRecording();
      } else if (data.status === "error" || data.status === "cancelled") {
        es.close();
        transcriptState.translateAllJobId = null;
        if (data.status === "error") showError(`Translation failed: ${data.message}`);
      }
    };
    es.onerror = () => {
      es.close();
      transcriptState.translateAllJobId = null;
    };
  } catch (err) {
    showError((err as Error).message);
  }
}

export async function setRecordingFolder(folderId: string): Promise<void> {
  const recId = transcriptState.recordingId;
  if (!recId) return;
  try {
    const fd = new FormData();
    fd.append("folder_id", folderId);
    await throwIfFailed(await fetch(`/api/recordings/${recId}`, { method: "PATCH", body: fd }), "Move failed.");
    if (transcriptState.currentRecording) transcriptState.currentRecording.folder_id = folderId || null;
  } catch (err) {
    showError(`Move failed: ${(err as Error).message}`);
  }
}

export async function toggleRecordingTag(tagId: string, add: boolean, tag?: { id: string; name: string; color_code: string }): Promise<void> {
  const recId = transcriptState.recordingId;
  if (!recId) return;
  try {
    await throwIfFailed(await fetch(`/api/recordings/${recId}/tags/${tagId}`, { method: add ? "POST" : "DELETE" }), "Tag update failed.");
    const current = transcriptState.currentRecording;
    if (current) {
      if (add && tag) {
        if (!current.tags.find((t) => t.id === tagId)) current.tags.push(tag);
      } else if (!add) {
        current.tags = current.tags.filter((t) => t.id !== tagId);
      }
    }
  } catch (err) {
    showError(`Tag update failed: ${(err as Error).message}`);
  }
}
