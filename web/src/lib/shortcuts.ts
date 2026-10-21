import { toggleShortcutsOverlay } from "./modals.svelte";

let installed = false;

export function initShortcuts(): void {
  if (installed) return;
  installed = true;

  window.addEventListener("keydown", (e) => {
    const active = document.activeElement as HTMLElement | null;
    if (
      active &&
      (active.tagName === "INPUT" ||
        active.tagName === "TEXTAREA" ||
        active.tagName === "SELECT" ||
        active.tagName === "BUTTON" ||
        active.isContentEditable)
    ) {
      return;
    }

    switch (e.key) {
      case "?":
        toggleShortcutsOverlay();
        break;
      case "Escape":
        toggleShortcutsOverlay(false);
        break;
    }
  });
}
