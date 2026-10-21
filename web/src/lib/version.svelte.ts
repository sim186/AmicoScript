const REPO_BASE = "https://github.com/sim186/AmicoScript";
const RAW_VERSION_URL = "https://raw.githubusercontent.com/sim186/AmicoScript/main/VERSION";

export const versionState = $state({
  version: "",
  githubUrl: REPO_BASE,
});

export const updateBannerState = $state({
  visible: false,
  text: "",
  url: "",
});

export async function fetchVersion(): Promise<void> {
  let v = "";
  try {
    const res = await fetch("/api/version");
    if (res.ok) v = ((await res.json()).version || "").trim();
  } catch {
    /* ignore, try fallbacks */
  }
  if (!v) {
    try {
      const r2 = await fetch("/VERSION");
      if (r2.ok) v = (await r2.text()).trim();
    } catch {
      /* ignore */
    }
  }
  if (!v) {
    try {
      const r3 = await fetch(RAW_VERSION_URL);
      if (r3.ok) v = (await r3.text()).trim();
    } catch {
      /* ignore */
    }
  }
  versionState.version = v || "n/a";
  versionState.githubUrl = v ? `${REPO_BASE}/releases/tag/${v.startsWith("v") ? v : "v" + v}` : REPO_BASE;
}

export async function fetchLatestRelease(): Promise<void> {
  try {
    const res = await fetch("/api/latest-release");
    if (!res.ok) return;
    const j = await res.json();
    if (j.update_available) {
      const latest = j.latest || {};
      const local = j.local_version || "";
      const name = latest.name || latest.tag_name || "New release";
      updateBannerState.text = `Update available: ${name} (local: ${local || "n/a"})`;
      updateBannerState.url = latest.html_url || `${REPO_BASE}/releases`;
      updateBannerState.visible = true;
    } else {
      updateBannerState.visible = false;
    }
  } catch {
    /* ignore network errors */
  }
}

export function dismissUpdateBanner(): void {
  updateBannerState.visible = false;
}
