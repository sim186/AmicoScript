import { commandHaystack, listCommands, type Command } from "./commands";
import { rank } from "./fuzzy";
import { clientLog } from "./consoleLog.svelte";
import { switchTab } from "./session.svelte";

const SEARCH_DEBOUNCE_MS = 180;
const MAX_COMMANDS = 6;
const MAX_MIXED_COMMANDS = 3;
const MAX_RECORDINGS = 8;

const MODES: Record<string, "commands" | "recordings"> = { "/": "commands", "@": "recordings" };

const KIND_LABEL: Record<string, string> = {
  transcript: "Transcript",
  summary: "Summary",
  title: "Name",
  tag: "Tag",
  folder: "Folder",
};

export function escapeHtml(text: string): string {
  return String(text ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);
}

/**
 * /api/search wraps matches in <mark>, so a snippet cannot simply be assigned
 * as HTML — it is escaped whole and only those two tags are put back, so a
 * transcript that happens to contain "<script>" can't run as one.
 */
export function markedSnippet(text: string): string {
  return escapeHtml(text).replaceAll("&lt;mark&gt;", "<mark>").replaceAll("&lt;/mark&gt;", "</mark>");
}

export function fmtDur(seconds: number): string {
  const s = Math.max(0, Math.round(seconds || 0));
  const m = Math.floor(s / 60);
  const sec = s % 60;
  return `${m}:${String(sec).padStart(2, "0")}`;
}

export interface PaletteRow {
  type: "command" | "recording" | "search-all";
  section: string;
  title: string;
  subtitleHtml?: string;
  metaHtml?: string;
  disabled?: boolean;
  run?: () => void;
  filename?: string;
  kind?: string;
  duration?: number;
  status?: string;
  id?: string;
  query?: string;
}

export const paletteState = $state({
  open: false,
  query: "",
  rows: [] as PaletteRow[],
  activeIndex: 0,
  searching: false,
  pendingRecordingId: null as string | null,
  pendingSearchQuery: null as string | null,
});

let searchTimer: ReturnType<typeof setTimeout> | undefined;
let requestSeq = 0;
let remote: { query: string | null; results: any[] } = { query: null, results: [] };

function modeOf(raw: string): "commands" | "recordings" | "all" {
  return MODES[raw.trim()[0]] || "all";
}

function queryOf(raw: string): string {
  const trimmed = raw.trim();
  return (MODES[trimmed[0]] ? trimmed.slice(1) : trimmed).trim();
}

function commandRows(query: string, loose = false): PaletteRow[] {
  const needle = query.toLowerCase();
  const ranked = rank(query, listCommands(), commandHaystack).filter(
    ({ item }) => loose || !needle || commandHaystack(item).toLowerCase().includes(needle),
  );
  const limit = !needle ? ranked.length : loose ? MAX_COMMANDS : MAX_MIXED_COMMANDS;
  return ranked.slice(0, limit).map(({ item }) => ({
    type: "command",
    section: item.section,
    title: item.title,
    subtitleHtml: escapeHtml(item.hint || ""),
    disabled: item.disabled,
    run: item.run,
  }));
}

function recordingSubtitle(hit: any): string {
  if (hit.kind === "title") {
    return hit.title === hit.filename || (hit.alias || hit.filename) === hit.filename ? "" : escapeHtml(hit.filename);
  }
  return markedSnippet(hit.snippet || "");
}

function recordingMeta(hit: any): string {
  const badges = ((hit.matched_in?.length ? hit.matched_in : [hit.kind]) as string[])
    .map((kind) => KIND_LABEL[kind] || kind)
    .map((label) => `<span class="palette-badge">${escapeHtml(label)}</span>`)
    .join("");
  const dur = hit.duration ? `<span class="palette-dur">${escapeHtml(fmtDur(hit.duration))}</span>` : "";
  return badges + dur;
}

function recordingRows(): PaletteRow[] {
  return remote.results.slice(0, MAX_RECORDINGS).map((hit) => ({
    type: "recording",
    section: "Library",
    title: hit.alias || hit.filename,
    filename: hit.filename,
    subtitleHtml: recordingSubtitle(hit),
    metaHtml: recordingMeta(hit),
    kind: hit.kind,
    duration: hit.duration,
    status: hit.status,
    id: hit.recording_id,
  }));
}

function searchAllRow(query: string): PaletteRow {
  return { type: "search-all", section: "Library", title: `Show every result for "${query}" in the library`, query };
}

function groupBySection(rows: PaletteRow[]): PaletteRow[] {
  const sections = new Map<string, PaletteRow[]>();
  for (const row of rows) {
    if (!sections.has(row.section)) sections.set(row.section, []);
    sections.get(row.section)!.push(row);
  }
  return [...sections.values()].flat();
}

function buildRows(): PaletteRow[] {
  const raw = paletteState.query;
  const mode = modeOf(raw);
  const query = queryOf(raw);

  if (mode === "commands") return groupBySection(commandRows(query, true));
  if (mode === "recordings") return query ? [...recordingRows(), searchAllRow(query)] : [];

  if (!query) return groupBySection(commandRows("", true));
  return groupBySection([...commandRows(query), ...recordingRows(), searchAllRow(query)]);
}

function render(): void {
  paletteState.rows = buildRows();
  if (paletteState.activeIndex >= paletteState.rows.length) {
    paletteState.activeIndex = Math.max(0, paletteState.rows.length - 1);
  }
}

async function runSearch(): Promise<void> {
  const raw = paletteState.query;
  const query = queryOf(raw);
  const mode = modeOf(raw);
  if (!query || mode === "commands") {
    remote = { query, results: [] };
    render();
    return;
  }

  const seq = ++requestSeq;
  paletteState.searching = true;
  render();
  try {
    const res = await fetch(`/api/search?q=${encodeURIComponent(query)}&limit=${MAX_RECORDINGS}`);
    if (seq !== requestSeq) return;
    remote = { query, results: res.ok ? await res.json() : [] };
  } catch {
    if (seq !== requestSeq) return;
    remote = { query, results: [] };
  }
  paletteState.searching = false;
  render();
}

function scheduleSearch(): void {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(runSearch, SEARCH_DEBOUNCE_MS);
}

export function openCommandPalette(prefill = ""): void {
  paletteState.open = true;
  paletteState.query = prefill;
  paletteState.activeIndex = 0;
  remote = { query: null, results: [] };
  render();
  if (queryOf(prefill)) scheduleSearch();
}

export function closeCommandPalette(): void {
  paletteState.open = false;
  clearTimeout(searchTimer);
  requestSeq++;
}

export function onPaletteInput(value: string): void {
  paletteState.query = value;
  paletteState.activeIndex = 0;
  render();
  scheduleSearch();
}

export function movePaletteSelection(delta: number): void {
  if (!paletteState.rows.length) return;
  paletteState.activeIndex = (paletteState.activeIndex + delta + paletteState.rows.length) % paletteState.rows.length;
}

export function activatePaletteRow(index: number): void {
  const row = paletteState.rows[index];
  if (!row || row.disabled) return;
  closeCommandPalette();

  switch (row.type) {
    case "command":
      clientLog(`Palette: ${row.title}`);
      row.run?.();
      break;
    case "recording":
      paletteState.pendingRecordingId = row.id || null;
      paletteState.pendingSearchQuery = null;
      switchTab("library");
      break;
    case "search-all":
      paletteState.pendingRecordingId = null;
      paletteState.pendingSearchQuery = row.query || null;
      switchTab("library");
      break;
  }
}

export function paletteEnterPressed(): void {
  if (!paletteState.rows.length) {
    clearTimeout(searchTimer);
    runSearch();
    return;
  }
  activatePaletteRow(paletteState.activeIndex);
}
