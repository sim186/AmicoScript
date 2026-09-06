import { throwIfFailed } from "./apiError";
import { fetchFolders } from "./folders.svelte";
import { fetchTags, tagsState } from "./tags.svelte";
import { session, switchTab } from "./session.svelte";
import { openRecording as openRecordingInTranscript, resetTranscript, transcriptState } from "./transcript.svelte";
import { attachToJob } from "./queueWidget.svelte";

export interface RecordingTagRef {
  id: string;
  name: string;
  color_code: string;
}

export interface Recording {
  id: string;
  filename: string;
  alias: string | null;
  file_path: string;
  duration: number | null;
  folder_id: string | null;
  status: string;
  status_detail?: string | null;
  source?: string | null;
  created_at: number;
  transcription_options?: Record<string, unknown>;
  tags: RecordingTagRef[];
}

export const libraryState = $state({
  recordings: [] as Recording[],
  selectedFolderId: null as string | null,
  selectedTagId: null as string | null,
  sort: "created_at" as "created_at" | "filename" | "duration",
  order: "desc" as "asc" | "desc",
  selectedIds: new Set<string>(),
  lastSelectedIdx: null as number | null,
  headerLabel: "All recordings",
  errorMessage: "",
});

function showError(msg: string): void {
  libraryState.errorMessage = msg;
}

export async function fetchLibrary(): Promise<void> {
  const params = new URLSearchParams({ sort: libraryState.sort, order: libraryState.order, limit: "100" });
  if (libraryState.selectedFolderId) params.set("folder_id", libraryState.selectedFolderId);
  if (libraryState.selectedTagId) params.set("tag_id", libraryState.selectedTagId);
  try {
    const res = await fetch(`/api/library?${params}`);
    if (!res.ok) return;
    const recs: Recording[] = await res.json();
    libraryState.recordings = recs;
    const validIds = new Set(recs.map((r) => r.id));
    libraryState.selectedIds = new Set([...libraryState.selectedIds].filter((id) => validIds.has(id)));
  } catch {
    /* offline */
  }
}

export function toggleSelectAll(checked: boolean): void {
  libraryState.selectedIds = checked ? new Set(libraryState.recordings.map((r) => r.id)) : new Set();
  if (!checked) libraryState.lastSelectedIdx = null;
}

export function toggleCardSelection(id: string, checked: boolean): void {
  const next = new Set(libraryState.selectedIds);
  if (checked) next.add(id);
  else next.delete(id);
  libraryState.selectedIds = next;
  const idx = libraryState.recordings.findIndex((r) => r.id === id);
  if (idx !== -1) libraryState.lastSelectedIdx = checked ? idx : null;
}

export function selectRange(fromIdx: number, toIdx: number): void {
  const [lo, hi] = fromIdx < toIdx ? [fromIdx, toIdx] : [toIdx, fromIdx];
  const next = new Set(libraryState.selectedIds);
  for (let i = lo; i <= hi; i++) {
    const rec = libraryState.recordings[i];
    if (rec) next.add(rec.id);
  }
  libraryState.selectedIds = next;
}

export function clearSelection(): void {
  libraryState.selectedIds = new Set();
  libraryState.lastSelectedIdx = null;
}

export async function openRecording(rec: Recording): Promise<boolean> {
  const opened = await openRecordingInTranscript(rec);
  if (opened) {
    session.transcriptEnabled = true;
    switchTab("transcript");
  }
  return opened;
}

export async function saveAlias(recId: string, alias: string): Promise<void> {
  try {
    const fd = new FormData();
    fd.append("alias", alias.trim());
    const res = await throwIfFailed(await fetch(`/api/recordings/${recId}`, { method: "PATCH", body: fd }), "Rename failed.");
    const updated = await res.json();
    const rec = libraryState.recordings.find((r) => r.id === recId);
    if (rec) rec.alias = updated.alias;
  } catch (err) {
    showError(`Rename failed: ${(err as Error).message}`);
  }
}

export async function moveRecordingToFolder(recordingId: string, folderId: string): Promise<void> {
  try {
    const fd = new FormData();
    fd.append("folder_id", folderId);
    await throwIfFailed(await fetch(`/api/recordings/${recordingId}`, { method: "PATCH", body: fd }), "Move failed.");
    const rec = libraryState.recordings.find((r) => r.id === recordingId);
    if (rec) rec.folder_id = folderId || null;
    if (libraryState.selectedFolderId && libraryState.selectedFolderId !== folderId) {
      libraryState.recordings = libraryState.recordings.filter((r) => r.id !== recordingId);
    }
    fetchFolders();
    fetchTags();
  } catch (err) {
    showError(`Move failed: ${(err as Error).message}`);
  }
}

export async function toggleRecordingTag(recordingId: string, tagId: string, add: boolean): Promise<void> {
  try {
    await throwIfFailed(
      await fetch(`/api/recordings/${recordingId}/tags/${tagId}`, { method: add ? "POST" : "DELETE" }),
      "Tag update failed.",
    );
    await fetchLibrary();
    fetchTags();
  } catch (err) {
    showError(`Tag update failed: ${(err as Error).message}`);
  }
}

export async function deleteRecording(recordingId: string): Promise<void> {
  try {
    await throwIfFailed(await fetch(`/api/recordings/${recordingId}`, { method: "DELETE" }), "Delete failed.");
    if (transcriptState.recordingId === recordingId) resetTranscript();
    await fetchLibrary();
  } catch (err) {
    showError(`Delete failed: ${(err as Error).message}`);
  }
}

export async function bulkDelete(): Promise<void> {
  const ids = [...libraryState.selectedIds];
  if (!ids.length) return;
  let failed = 0;
  for (const id of ids) {
    try {
      const res = await fetch(`/api/recordings/${id}`, { method: "DELETE" });
      if (!res.ok) failed++;
      else if (transcriptState.recordingId === id) resetTranscript();
    } catch {
      failed++;
    }
  }
  clearSelection();
  if (failed) showError(`${failed} deletion${failed > 1 ? "s" : ""} failed.`);
  await fetchLibrary();
}

export async function bulkMoveToFolder(folderId: string): Promise<void> {
  const ids = [...libraryState.selectedIds];
  if (!ids.length) return;
  let failed = 0;
  for (const id of ids) {
    try {
      const fd = new FormData();
      fd.append("folder_id", folderId);
      const res = await fetch(`/api/recordings/${id}`, { method: "PATCH", body: fd });
      if (!res.ok) {
        failed++;
        continue;
      }
      const rec = libraryState.recordings.find((r) => r.id === id);
      if (rec) rec.folder_id = folderId || null;
    } catch {
      failed++;
    }
  }
  if (libraryState.selectedFolderId && libraryState.selectedFolderId !== folderId) {
    libraryState.recordings = libraryState.recordings.filter((r) => !ids.includes(r.id));
  }
  clearSelection();
  if (failed) showError(`${failed} move${failed > 1 ? "s" : ""} failed.`);
  fetchFolders();
  fetchTags();
}

export async function bulkAssignTag(tagId: string): Promise<void> {
  const ids = [...libraryState.selectedIds];
  const tag = tagsState.tags.find((t) => t.id === tagId);
  if (!ids.length || !tag) return;
  const selectedRecs = libraryState.recordings.filter((r) => ids.includes(r.id));
  const allHave = selectedRecs.every((r) => r.tags.some((t) => t.id === tagId));
  const method = allHave ? "DELETE" : "POST";
  let failed = 0;
  for (const id of ids) {
    try {
      const res = await fetch(`/api/recordings/${id}/tags/${tagId}`, { method });
      if (!res.ok) {
        failed++;
        continue;
      }
      const rec = libraryState.recordings.find((r) => r.id === id);
      if (rec) {
        if (method === "POST") {
          if (!rec.tags.find((t) => t.id === tagId)) rec.tags.push(tag);
        } else {
          rec.tags = rec.tags.filter((t) => t.id !== tagId);
        }
      }
    } catch {
      failed++;
    }
  }
  if (failed) showError(`${failed} tag operation${failed > 1 ? "s" : ""} failed.`);
  fetchTags();
}

function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export async function bulkExport(fmt: string, wikilinks: boolean): Promise<void> {
  const ids = [...libraryState.selectedIds];
  if (!ids.length) return;

  if (fmt === "md") {
    try {
      const res = await fetch("/api/recordings/bulk-export/md", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ids, wikilinks }),
      });
      if (!res.ok) {
        showError(`Export failed: HTTP ${res.status}`);
        return;
      }
      const filename =
        ids.length === 1
          ? (libraryState.recordings.find((r) => r.id === ids[0])?.filename || "transcript").replace(/\.[^.]+$/, "")
          : "transcripts";
      downloadBlob(await res.blob(), `${filename}.md`);
    } catch (err) {
      showError(`Export failed: ${(err as Error).message}`);
    }
    return;
  }

  for (const id of ids) {
    try {
      const rec = libraryState.recordings.find((r) => r.id === id);
      const baseName = (rec?.filename || id).replace(/\.[^.]+$/, "");
      const res = await fetch(`/api/recordings/${id}/export/${fmt}`);
      if (!res.ok) {
        showError(`Export failed for ${baseName}: HTTP ${res.status}`);
        continue;
      }
      downloadBlob(await res.blob(), `${baseName}.${fmt}`);
      await new Promise((r) => setTimeout(r, 150));
    } catch (err) {
      showError(`Export failed: ${(err as Error).message}`);
    }
  }
}

export async function retryRecording(recordingId: string): Promise<void> {
  try {
    const res = await fetch(`/api/recordings/${recordingId}/retry`, { method: "POST" });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      showError(body.detail || "Could not queue this recording again.");
      return;
    }
    if (body.job_id) attachToJob(body.job_id);
    await fetchLibrary();
  } catch (err) {
    showError((err as Error).message);
  }
}

export async function showSearchResultsInLibrary(q: string): Promise<void> {
  if (!q) {
    libraryState.selectedFolderId = null;
    libraryState.selectedTagId = null;
    libraryState.headerLabel = "All recordings";
    await fetchLibrary();
    return;
  }
  try {
    const res = await fetch(`/api/search?q=${encodeURIComponent(q)}&limit=100`);
    if (!res.ok) return;
    const rows: any[] = await res.json();
    libraryState.selectedFolderId = null;
    libraryState.selectedTagId = null;
    libraryState.headerLabel = `Search results for "${q}"`;
    if (rows.length === 0) {
      libraryState.recordings = [];
      return;
    }
    const recs = (
      await Promise.all(
        rows.map((r) =>
          fetch(`/api/recordings/${r.recording_id}`)
            .then((rp) => (rp.ok ? rp.json() : null))
            .catch(() => null),
        ),
      )
    ).filter(Boolean);
    libraryState.recordings = recs;
    fetchFolders();
    fetchTags();
  } catch {
    /* best-effort */
  }
}
