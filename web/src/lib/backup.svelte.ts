import { clientLog } from "./consoleLog.svelte";

export const backupState = $state({
  feedback: "",
  tone: "info" as "info" | "ok" | "error",
  exporting: false,
  importing: false,
});

export async function exportLibrary(includeAudio: boolean): Promise<void> {
  backupState.exporting = true;
  backupState.tone = "info";
  backupState.feedback = "Building the bundle. With audio this can take a while.";
  try {
    const res = await fetch(`/api/library/export?include_audio=${includeAudio}`);
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || `Export failed (${res.status})`);
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    const stamp = new Date().toISOString().slice(0, 10);
    a.download = `amicoscript-library-${stamp}.zip`;
    a.click();
    URL.revokeObjectURL(url);
    backupState.tone = "ok";
    backupState.feedback = `Exported ${(blob.size / 1024 / 1024).toFixed(1)} MB.`;
    clientLog("Library exported");
  } catch (err) {
    backupState.tone = "error";
    backupState.feedback = (err as Error).message;
    clientLog(`Library export failed: ${(err as Error).message}`, "ERROR");
  } finally {
    backupState.exporting = false;
  }
}

export async function importLibrary(file: File | undefined, overwrite: boolean): Promise<void> {
  if (!file) return;
  backupState.importing = true;
  backupState.tone = "info";
  backupState.feedback = "Importing…";
  try {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("mode", overwrite ? "overwrite" : "skip");
    const res = await fetch("/api/library/import", { method: "POST", body: fd });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.detail || `Import failed (${res.status})`);

    const counts = body.imported || {};
    backupState.tone = "ok";
    backupState.feedback = `Imported ${counts.recordings || 0} recording(s), ${counts.audio || 0} audio file(s).`;
    clientLog(`Library imported: ${JSON.stringify(counts)}`);
    window.dispatchEvent(new CustomEvent("amicoscript:library-changed"));
  } catch (err) {
    backupState.tone = "error";
    backupState.feedback = (err as Error).message;
    clientLog(`Library import failed: ${(err as Error).message}`, "ERROR");
  } finally {
    backupState.importing = false;
  }
}
