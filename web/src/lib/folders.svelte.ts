import { throwIfFailed } from "./apiError";
import { libraryState, fetchLibrary } from "./library.svelte";
import { fetchTags } from "./tags.svelte";
import { switchTab, session } from "./session.svelte";

export interface Folder {
  id: string;
  name: string;
  parent_id: string | null;
  color_code: string;
  count?: number;
}

export const foldersState = $state({
  folders: [] as Folder[],
  errorMessage: "",
});

function showError(msg: string): void {
  foldersState.errorMessage = msg;
}

export async function fetchFolders(): Promise<void> {
  try {
    const res = await fetch("/api/folders");
    if (!res.ok) return;
    foldersState.folders = await res.json();
  } catch {
    /* offline */
  }
}

/** All folders in tree order (roots first, then their children depth-first). */
export function visibleFolders(): Folder[] {
  const byParent = new Map<string, Folder[]>();
  foldersState.folders.forEach((f) => {
    const key = f.parent_id || "__root__";
    if (!byParent.has(key)) byParent.set(key, []);
    byParent.get(key)!.push(f);
  });
  const visible: Folder[] = [];
  const queue = [...(byParent.get("__root__") || [])];
  while (queue.length) {
    const f = queue.shift()!;
    visible.push(f);
    (byParent.get(f.id) || []).forEach((c) => queue.push(c));
  }
  return visible;
}

export function childrenOf(folderId: string | null): Folder[] {
  return foldersState.folders.filter((f) => f.parent_id === folderId);
}

export function selectFolder(folderId: string | null): void {
  libraryState.selectedFolderId = folderId;
  libraryState.selectedTagId = null;
  const folder = folderId ? foldersState.folders.find((f) => f.id === folderId) : null;
  libraryState.headerLabel = folderId ? folder?.name || "Folder" : "All recordings";
  fetchLibrary();
  fetchTags();
  if (session.activeTab !== "library") switchTab("library");
}

export async function createFolder(name: string, parentId: string | null, color: string): Promise<void> {
  if (!name) return;
  const fd = new FormData();
  fd.append("name", name);
  if (parentId) fd.append("parent_id", parentId);
  if (color) fd.append("color_code", color);
  try {
    await throwIfFailed(await fetch("/api/folders", { method: "POST", body: fd }), "Create folder failed.");
    await fetchFolders();
    await fetchLibrary();
  } catch (err) {
    showError((err as Error).message);
    throw err;
  }
}

export async function updateFolder(id: string, name: string, color: string): Promise<void> {
  const fd = new FormData();
  fd.append("name", name);
  fd.append("color_code", color);
  try {
    await throwIfFailed(await fetch(`/api/folders/${id}`, { method: "PATCH", body: fd }), "Could not rename the folder.");
    await fetchFolders();
    await fetchTags();
    await fetchLibrary();
  } catch (err) {
    showError((err as Error).message);
    throw err;
  }
}

export async function deleteFolder(folderId: string): Promise<void> {
  try {
    await throwIfFailed(await fetch(`/api/folders/${folderId}`, { method: "DELETE" }), "Delete folder failed.");
    if (libraryState.selectedFolderId === folderId) selectFolder(null);
    await fetchFolders();
    await fetchLibrary();
  } catch (err) {
    showError(`Delete folder failed: ${(err as Error).message}`);
  }
}
