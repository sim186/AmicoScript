import { clientLog } from "./consoleLog.svelte";

export type TabName = "transcribe" | "transcript" | "library";

export const session = $state({
  activeTab: "transcribe" as TabName,
  transcriptEnabled: false,
  drawerOpen: true,
});

export const viewport = $state({ mobile: false });

export function isMobile(): boolean {
  return window.innerWidth < 768;
}

let resizeListenerInstalled = false;

/** Keeps viewport.mobile reactive so components re-render on a breakpoint crossing. */
export function initViewportTracking(): void {
  viewport.mobile = isMobile();
  if (resizeListenerInstalled) return;
  resizeListenerInstalled = true;
  window.addEventListener("resize", () => {
    const nowMobile = isMobile();
    if (nowMobile === viewport.mobile) return;
    viewport.mobile = nowMobile;
    if (nowMobile) {
      session.drawerOpen = false;
    } else {
      let stored: string | null = null;
      try {
        stored = localStorage.getItem("drawerOpen");
      } catch {
        /* ignore */
      }
      session.drawerOpen = stored !== "0";
    }
  });
}

export function openDrawer(): void {
  session.drawerOpen = true;
  try {
    localStorage.setItem("drawerOpen", "1");
  } catch {
    /* private-browsing / storage disabled */
  }
}

export function closeDrawer(): void {
  session.drawerOpen = false;
  try {
    localStorage.setItem("drawerOpen", "0");
  } catch {
    /* private-browsing / storage disabled */
  }
}

export function toggleDrawer(): void {
  if (session.drawerOpen) closeDrawer();
  else openDrawer();
}

export function restoreDrawerState(): void {
  if (isMobile()) {
    session.drawerOpen = false;
    return;
  }
  try {
    session.drawerOpen = localStorage.getItem("drawerOpen") !== "0";
  } catch {
    session.drawerOpen = true;
  }
}

export function switchTab(name: TabName): void {
  if (name === "transcript" && !session.transcriptEnabled) return;
  clientLog(`Tab switched → ${name}`);
  session.activeTab = name;
  if (isMobile()) closeDrawer();
}
