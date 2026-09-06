import { PALETTE } from "./palette";
import { createFolder, updateFolder, foldersState } from "./folders.svelte";
import { createTag, updateTag } from "./tags.svelte";

export type EntityType = "folder" | "tag";

export const entityDialogState = $state({
  open: false,
  type: "folder" as EntityType,
  id: null as string | null,
  parentId: null as string | null,
  name: "",
  color: PALETTE[0],
  saving: false,
  error: "",
});

export function openEntityDialog(
  type: EntityType,
  obj?: { id: string; name: string; color_code: string } | null,
  parentId?: string | null,
): void {
  entityDialogState.open = true;
  entityDialogState.type = type;
  entityDialogState.id = obj?.id || null;
  entityDialogState.parentId = parentId || null;
  entityDialogState.name = obj?.name || "";
  entityDialogState.color = obj?.color_code || PALETTE[0];
  entityDialogState.error = "";
}

export function closeEntityDialog(): void {
  entityDialogState.open = false;
}

export function parentFolderName(): string {
  if (!entityDialogState.parentId) return "All recordings";
  return foldersState.folders.find((f) => f.id === entityDialogState.parentId)?.name || "Folder";
}

export async function saveEntityDialog(): Promise<void> {
  const name = entityDialogState.name.trim();
  if (!name) {
    entityDialogState.error = "Name required";
    return;
  }
  entityDialogState.saving = true;
  entityDialogState.error = "";
  try {
    if (entityDialogState.type === "folder") {
      if (entityDialogState.id) await updateFolder(entityDialogState.id, name, entityDialogState.color);
      else await createFolder(name, entityDialogState.parentId, entityDialogState.color);
    } else {
      if (entityDialogState.id) await updateTag(entityDialogState.id, name, entityDialogState.color);
      else await createTag(name, entityDialogState.color);
    }
    closeEntityDialog();
  } catch (err) {
    entityDialogState.error = (err as Error).message || String(err);
  } finally {
    entityDialogState.saving = false;
  }
}
