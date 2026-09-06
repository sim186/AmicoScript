<script lang="ts">
  import { libraryState } from "$lib/library.svelte";
  import { childrenOf, selectFolder, deleteFolder, type Folder } from "$lib/folders.svelte";
  import { moveRecordingToFolder } from "$lib/library.svelte";
  import { openEntityDialog } from "$lib/entityDialog.svelte";
  import FolderTreeItem from "./FolderTreeItem.svelte";

  let { folder }: { folder: Folder } = $props();

  let expanded = $state(true);
  let dragOver = $state(false);
  let menuOpen = $state(false);

  const children = $derived(childrenOf(folder.id));
  const isActive = $derived(libraryState.selectedFolderId === folder.id);

  function onDrop(e: DragEvent): void {
    e.preventDefault();
    dragOver = false;
    const recId = e.dataTransfer?.getData("text/plain");
    if (recId) moveRecordingToFolder(recId, folder.id);
  }

  function onDelete(): void {
    if (window.confirm("Delete folder? Recordings inside will be moved to All Recordings.")) deleteFolder(folder.id);
  }
</script>

<div>
  <div
    role="treeitem"
    aria-selected={isActive}
    tabindex="0"
    title="Double-click to rename · Right-click for options"
    class="flex cursor-pointer select-none items-center gap-1 rounded-md px-1.5 py-1 text-sm transition {isActive
      ? 'bg-brand-muted font-semibold text-brand'
      : 'text-slate-600 hover:bg-slate-100'} {dragOver ? 'bg-indigo-50' : ''}"
    onclick={() => selectFolder(folder.id)}
    onkeydown={(e) => e.key === "Enter" && selectFolder(folder.id)}
    ondblclick={(e) => {
      e.stopPropagation();
      openEntityDialog("folder", folder);
    }}
    oncontextmenu={(e) => {
      e.preventDefault();
      menuOpen = true;
    }}
    ondragover={(e) => {
      e.preventDefault();
      dragOver = true;
    }}
    ondragleave={() => (dragOver = false)}
    ondrop={onDrop}
  >
    {#if children.length}
      <button
        type="button"
        onclick={(e) => {
          e.stopPropagation();
          expanded = !expanded;
        }}
        class="flex h-3 w-3 shrink-0 items-center justify-center text-slate-400 transition"
        style="transform: rotate({expanded ? 90 : 0}deg)"
        aria-label={expanded ? "Collapse" : "Expand"}
      >
        <svg class="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M9 5l7 7-7 7" /></svg>
      </button>
    {:else}
      <span class="w-3 shrink-0"></span>
    {/if}
    <svg class="h-4 w-4 shrink-0 text-slate-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
      <path stroke-linecap="round" stroke-linejoin="round" d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V7z" />
    </svg>
    {#if folder.color_code}<span class="mr-0.5 h-2.5 w-2.5 shrink-0 rounded-full" style="background:{folder.color_code}"></span>{/if}
    <span class="flex-1 truncate">{folder.name}</span>
    {#if typeof folder.count !== "undefined"}<span class="ml-2 text-xs text-slate-400">({folder.count})</span>{/if}
  </div>

  {#if menuOpen}
    <div role="presentation" class="fixed inset-0 z-40" onclick={() => (menuOpen = false)}></div>
    <div class="relative z-50 -mt-1 ml-4 w-40 rounded-lg border border-slate-200 bg-white py-1 text-sm shadow-lg">
      <button
        class="block w-full px-4 py-1.5 text-left hover:bg-slate-50"
        onclick={() => {
          menuOpen = false;
          openEntityDialog("folder", folder);
        }}
      >
        Rename / Color
      </button>
      <button
        class="block w-full px-4 py-1.5 text-left text-red-500 hover:bg-slate-50"
        onclick={() => {
          menuOpen = false;
          onDelete();
        }}
      >
        Delete
      </button>
    </div>
  {/if}

  {#if children.length && expanded}
    <div class="space-y-0.5 pl-3.5">
      {#each children as child (child.id)}
        <FolderTreeItem folder={child} />
      {/each}
    </div>
  {/if}
</div>
