import { clientLog } from "./consoleLog.svelte";

export const modalState = $state({
  shortcutsVisible: false,
  helpVisible: false,
  changelogVisible: false,
  changelogContent: "",
});

export function toggleShortcutsOverlay(show?: boolean): void {
  modalState.shortcutsVisible = typeof show === "boolean" ? show : !modalState.shortcutsVisible;
}

export function openHelpModal(): void {
  clientLog("Help modal opened");
  modalState.helpVisible = true;
}

export function closeHelpModal(): void {
  modalState.helpVisible = false;
}

const REPO_BLOB = "https://github.com/sim186/AmicoScript/blob/main/CHANGELOG.md";
const RAW_URL = "https://raw.githubusercontent.com/sim186/AmicoScript/main/CHANGELOG.md";
const LOCAL_URL = "/CHANGELOG.md";

export async function fetchAndShowChangelog(): Promise<void> {
  modalState.changelogContent = "Loading changelog…";
  try {
    let res = await fetch(RAW_URL);
    if (!res.ok) res = await fetch(LOCAL_URL);
    if (res.ok) {
      modalState.changelogContent = (await res.text()) || "No changelog available.";
      modalState.changelogVisible = true;
    } else {
      window.open(REPO_BLOB, "_blank", "noopener");
    }
  } catch {
    modalState.changelogContent = "Unable to load changelog.";
    modalState.changelogVisible = true;
    try {
      window.open(REPO_BLOB, "_blank", "noopener");
    } catch {
      /* ignore */
    }
  }
}

export function closeChangelogModal(): void {
  modalState.changelogVisible = false;
}

export function changelogGithubUrl(): string {
  return REPO_BLOB;
}

export function tuiCommand(): string {
  const origin = window.location.origin;
  const isDefault = origin === "http://127.0.0.1:8002" || origin === "http://localhost:8002";
  const urlFlag = isDefault ? "" : ` --api-url ${origin}`;
  return `pip install -r tui/requirements.txt && python -m tui${urlFlag}`;
}

export async function copyTuiCommand(): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(tuiCommand());
    return true;
  } catch {
    return false;
  }
}
