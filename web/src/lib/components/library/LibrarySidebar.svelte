<script lang="ts">
  import { libraryState, moveRecordingToFolder } from "$lib/library.svelte";
  import { childrenOf, selectFolder } from "$lib/folders.svelte";
  import { tagsState, filterByTag, deleteTag, type Tag } from "$lib/tags.svelte";
  import { openEntityDialog } from "$lib/entityDialog.svelte";
  import FolderTreeItem from "./FolderTreeItem.svelte";

  const rootFolders = $derived(childrenOf(null));
  let dragOverAll = $state(false);
  let tagMenu = $state<{ tag: Tag; x: number; y: number } | null>(null);

  function onDropAll(e: DragEvent): void {
    e.preventDefault();
    dragOverAll = false;
    const recId = e.dataTransfer?.getData("text/plain");
    if (recId) moveRecordingToFolder(recId, "");
  }

  function onTagContextMenu(e: MouseEvent, tag: Tag): void {
    e.preventDefault();
    tagMenu = { tag, x: e.clientX, y: e.clientY };
  }
</script>

<div class="flex flex-col gap-4 p-4 text-xs">
  <div>
    <div class="mb-2 flex items-center justify-between">
      <h2 class="text-xs font-semibold uppercase tracking-wider text-slate-500">Folders</h2>
      <button
        type="button"
        title="New folder"
        onclick={() => openEntityDialog("folder", null, libraryState.selectedFolderId)}
        class="rounded p-0.5 text-slate-400 transition hover:text-brand focus:outline-none"
      >
        <svg class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M12 4v16m8-8H4" /></svg>
      </button>
    </div>
    <div
      role="treeitem"
      aria-selected={!libraryState.selectedFolderId}
      tabindex="0"
      class="flex cursor-pointer select-none items-center gap-1 rounded-md px-1.5 py-1 text-sm transition {!libraryState.selectedFolderId
        ? 'bg-brand-muted font-semibold text-brand'
        : 'text-slate-600 hover:bg-slate-100'} {dragOverAll ? 'bg-indigo-50' : ''}"
      onclick={() => selectFolder(null)}
      onkeydown={(e) => e.key === "Enter" && selectFolder(null)}
      ondragover={(e) => {
        e.preventDefault();
        dragOverAll = true;
      }}
      ondragleave={() => (dragOverAll = false)}
      ondrop={onDropAll}
    >
      <svg class="h-4 w-4 shrink-0 text-slate-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
        <path stroke-linecap="round" stroke-linejoin="round" d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V7z" />
      </svg>
      <span class="flex-1">All recordings</span>
    </div>
    <div class="mt-0.5 space-y-0.5">
      {#each rootFolders as folder (folder.id)}
        <FolderTreeItem {folder} />
      {/each}
    </div>
  </div>

  <div>
    <div class="mb-2 flex items-center justify-between">
      <h2 class="text-xs font-semibold uppercase tracking-wider text-slate-500">Tags</h2>
      <button
        type="button"
        title="New tag"
        onclick={() => openEntityDialog("tag", null)}
        class="rounded p-0.5 text-slate-400 transition hover:text-brand focus:outline-none"
      >
        <svg class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M12 4v16m8-8H4" /></svg>
      </button>
    </div>
    <div class="flex flex-wrap gap-1.5">
      {#each tagsState.tags as tag (tag.id)}
        {@const disabled = !!libraryState.selectedFolderId && !tag.count}
        <span class="inline-flex items-center gap-1.5">
          <button
            type="button"
            title="Click to filter · Double-click to edit · Right-click for options"
            {disabled}
            onclick={() => filterByTag(tag.id)}
            ondblclick={(e) => {
              e.stopPropagation();
              openEntityDialog("tag", tag);
            }}
            oncontextmenu={(e) => onTagContextMenu(e, tag)}
            class="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-semibold text-white transition {disabled
              ? 'cursor-not-allowed opacity-40'
              : 'cursor-pointer'} {libraryState.selectedTagId === tag.id ? 'ring-2 ring-slate-400 ring-offset-1' : ''}"
            style="background:{tag.color_code}"
          >
            {tag.name}
          </button>
          {#if typeof tag.count !== "undefined"}<span class="text-xs text-slate-400">{tag.count}</span>{/if}
        </span>
      {/each}
    </div>
  </div>
</div>

{#if tagMenu}
  <div role="presentation" class="fixed inset-0 z-40" onclick={() => (tagMenu = null)}></div>
  <div class="fixed z-50 w-40 rounded-lg border border-slate-200 bg-white py-1 text-sm shadow-lg" style="left:{tagMenu.x}px; top:{tagMenu.y}px">
    <button
      class="block w-full px-4 py-1.5 text-left hover:bg-slate-50"
      onclick={() => {
        const t = tagMenu?.tag;
        tagMenu = null;
        if (t) openEntityDialog("tag", t);
      }}
    >
      Rename / Color
    </button>
    <button
      class="block w-full px-4 py-1.5 text-left text-red-500 hover:bg-slate-50"
      onclick={() => {
        const t = tagMenu?.tag;
        tagMenu = null;
        if (t && window.confirm(`Delete tag "${t.name}"?`)) deleteTag(t);
      }}
    >
      Delete
    </button>
  </div>
{/if}
