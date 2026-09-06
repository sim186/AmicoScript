import { throwIfFailed } from "./apiError";
import { libraryState, fetchLibrary } from "./library.svelte";

export interface Tag {
  id: string;
  name: string;
  color_code: string;
  count?: number;
}

export const tagsState = $state({
  tags: [] as Tag[],
  errorMessage: "",
});

function showError(msg: string): void {
  tagsState.errorMessage = msg;
}

export async function fetchTags(): Promise<void> {
  const params = new URLSearchParams();
  if (libraryState.selectedFolderId) params.set("folder_id", libraryState.selectedFolderId);
  try {
    const res = await fetch(`/api/tags${params.toString() ? `?${params}` : ""}`);
    if (!res.ok) return;
    tagsState.tags = await res.json();
  } catch {
    /* offline */
  }
}

export async function createTag(name: string, color: string): Promise<void> {
  if (!name) return;
  try {
    const fd = new FormData();
    fd.append("name", name);
    fd.append("color_code", color || "#6c63ff");
    await throwIfFailed(await fetch("/api/tags", { method: "POST", body: fd }), "Could not create the tag.");
    await fetchTags();
    await fetchLibrary();
  } catch (err) {
    showError((err as Error).message);
    throw err;
  }
}

export async function updateTag(id: string, name: string, color: string): Promise<void> {
  try {
    const fd = new FormData();
    fd.append("name", name);
    fd.append("color_code", color);
    await throwIfFailed(await fetch(`/api/tags/${id}`, { method: "PATCH", body: fd }), "Could not rename the tag.");
    await fetchTags();
    await fetchLibrary();
  } catch (err) {
    showError((err as Error).message);
    throw err;
  }
}

export async function deleteTag(tag: Tag): Promise<void> {
  try {
    const res = await fetch(`/api/tags/${tag.id}`, { method: "DELETE" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    if (libraryState.selectedTagId === tag.id) clearTagFilter();
    await fetchTags();
    await fetchLibrary();
  } catch (err) {
    showError(`Delete tag failed: ${(err as Error).message}`);
  }
}

export function filterByTag(tagId: string): void {
  if (libraryState.selectedTagId === tagId) {
    clearTagFilter();
    return;
  }
  libraryState.selectedTagId = tagId;
  fetchLibrary();
}

export function clearTagFilter(): void {
  libraryState.selectedTagId = null;
  fetchLibrary();
}
