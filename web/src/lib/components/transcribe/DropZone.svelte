<script lang="ts">
  import { jobState, handleFile, handleFiles, clearFile, startUrlTranscription } from "$lib/jobs.svelte";

  let fileInput: HTMLInputElement | undefined;
  let folderInput: HTMLInputElement | undefined;
  let dragging = $state(false);

  function isLikelyUrl(value: string): boolean {
    if (!value) return false;
    try {
      const u = new URL(value.trim());
      return u.protocol === "http:" || u.protocol === "https:";
    } catch {
      return false;
    }
  }

  async function collectFilesFromEntries(entries: any[]): Promise<File[]> {
    return new Promise((resolve) => {
      const files: File[] = [];
      let pending = 0;
      function readEntry(entry: any) {
        if (entry.isFile) {
          pending++;
          entry.file((file: File) => {
            files.push(file);
            pending--;
            if (pending === 0) resolve(files);
          });
        } else if (entry.isDirectory) {
          pending++;
          const reader = entry.createReader();
          function readBatch() {
            reader.readEntries((batch: any[]) => {
              if (batch.length === 0) {
                pending--;
                if (pending === 0) resolve(files);
                return;
              }
              batch.forEach(readEntry);
              readBatch();
            });
          }
          readBatch();
        }
      }
      if (entries.length === 0) {
        resolve(files);
        return;
      }
      entries.forEach(readEntry);
    });
  }

  function onFileInputChange() {
    if (!fileInput?.files?.length) return;
    const files = Array.from(fileInput.files);
    if (files.length === 1) handleFile(files[0]);
    else handleFiles(files);
    fileInput.value = "";
  }

  function onFolderInputChange() {
    if (!folderInput?.files?.length) return;
    const files = Array.from(folderInput.files);
    if (files.length === 1) handleFile(files[0]);
    else handleFiles(files);
    folderInput.value = "";
  }

  function onPasteLink(e: MouseEvent) {
    e.stopPropagation();
    const value = window.prompt("Paste a video URL (YouTube, TikTok, Instagram, Facebook, X, Vimeo, Twitch...)", "");
    if (value) startUrlTranscription(value);
  }

  async function onDrop(e: DragEvent) {
    e.preventDefault();
    dragging = false;
    const dt = e.dataTransfer;
    if (!dt) return;

    const droppedText = (dt.getData("text/uri-list") || dt.getData("text/plain") || "").trim();
    if (!dt.files.length && isLikelyUrl(droppedText)) {
      startUrlTranscription(droppedText);
      return;
    }

    const items = Array.from(dt.items || []);
    const entries = items.map((i: any) => i.webkitGetAsEntry?.()).filter(Boolean);
    if (entries.length && entries.some((en: any) => en.isDirectory)) {
      const files = await collectFilesFromEntries(entries);
      if (files.length === 1) handleFile(files[0]);
      else handleFiles(files);
      return;
    }

    const files = Array.from(dt.files);
    if (files.length === 1) handleFile(files[0]);
    else if (files.length > 1) handleFiles(files);
  }

  function onPaste(e: ClipboardEvent) {
    const text = (e.clipboardData?.getData("text/plain") || "").trim();
    if (isLikelyUrl(text)) {
      e.preventDefault();
      startUrlTranscription(text);
    }
  }

  function fmtBytes(b: number): string {
    return b < 1048576 ? `${(b / 1024).toFixed(0)} KB` : `${(b / 1048576).toFixed(1)} MB`;
  }
</script>

<div
  id="drop-zone"
  role="button"
  tabindex="0"
  onclick={() => fileInput?.click()}
  onkeydown={(e) => (e.key === "Enter" || e.key === " ") && fileInput?.click()}
  ondragover={(e) => {
    e.preventDefault();
    dragging = true;
  }}
  ondragleave={(e) => {
    if (!(e.currentTarget as HTMLElement).contains(e.relatedTarget as Node)) dragging = false;
  }}
  ondrop={onDrop}
  onpaste={onPaste}
  class="group relative flex flex-col items-center justify-center gap-4 rounded-2xl border-2 border-dashed bg-surface px-8 py-16 text-center transition-all duration-200 focus:outline-none focus:ring-2 focus:ring-brand cursor-pointer {dragging
    ? 'border-brand bg-brand-muted/20'
    : 'border-border hover:border-brand hover:bg-brand-muted/20'}"
>
  <input bind:this={fileInput} id="file-input" type="file" class="hidden" multiple onchange={onFileInputChange} />
  <input
    bind:this={folderInput}
    id="folder-input"
    type="file"
    class="hidden"
    webkitdirectory
    onchange={onFolderInputChange}
  />

  {#if !jobState.selectedFile}
    <svg class="h-10 w-10 text-ink-muted transition group-hover:text-brand" fill="none" stroke="currentColor" stroke-width="1.5" viewBox="0 0 24 24">
      <path stroke-linecap="round" stroke-linejoin="round" d="M12 16.5V9.75m0 0l-3.75 3.75M12 9.75l3.75 3.75M6.75 19.5a4.5 4.5 0 01-1.41-8.775 5.25 5.25 0 0110.233-2.33 3 3 0 013.758 3.848A3.752 3.752 0 0118 19.5H6.75z" />
    </svg>
    <div>
      <p class="text-base font-medium text-ink">Drop audio or video files here</p>
      <p class="mt-1 text-sm text-ink-muted">or click to browse — MP3, WAV, M4A, OGG, FLAC, MP4, MOV, MKV, OPUS</p>
    </div>
    <div class="flex items-center gap-3">
      <button
        id="folder-btn"
        onclick={(e) => {
          e.stopPropagation();
          folderInput?.click();
        }}
        class="rounded-lg border border-border px-3 py-1.5 text-xs text-ink-muted transition hover:border-brand hover:text-brand"
      >
        Choose a folder
      </button>
      <button
        id="paste-link-btn"
        onclick={onPasteLink}
        class="rounded-lg border border-border px-3 py-1.5 text-xs text-ink-muted transition hover:border-brand hover:text-brand"
      >
        Paste a video URL
      </button>
    </div>
  {:else}
    <div id="file-info" class="flex items-center gap-3">
      <svg class="h-8 w-8 text-brand" fill="none" stroke="currentColor" stroke-width="1.5" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
      </svg>
      <div class="text-left">
        <p id="file-name" class="text-sm font-medium text-ink">{jobState.selectedFile.name}</p>
        <p id="file-size" class="text-xs text-ink-muted">{fmtBytes(jobState.selectedFile.size)}</p>
      </div>
      <button
        id="clear-file-btn"
        onclick={(e) => {
          e.stopPropagation();
          clearFile();
        }}
        class="ml-2 text-ink-muted transition hover:text-red-300"
        aria-label="Remove file"
      >
        <svg class="h-4 w-4" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
        </svg>
      </button>
    </div>
  {/if}

  <div
    id="drop-overlay"
    class="pointer-events-none absolute inset-0 flex scale-[0.98] items-center justify-center rounded-2xl border-2 border-brand bg-surface/90 opacity-0 backdrop-blur-sm transition-all duration-200"
    style={dragging ? "opacity:1;transform:scale(1)" : ""}
  >
    <p class="text-sm font-medium text-brand">Drop to queue</p>
  </div>
</div>
