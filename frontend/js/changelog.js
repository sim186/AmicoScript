// In-app changelog viewer.
//
// Part of the AmicoScript frontend. No build step: these are plain ES
// modules loaded directly by the browser via <script type="module">.

export async function fetchAndShowChangelog() {
  const repoBlob = 'https://github.com/sim186/AmicoScript/blob/main/CHANGELOG.md';
  const rawUrl = 'https://raw.githubusercontent.com/sim186/AmicoScript/main/CHANGELOG.md';
  const localUrl = '/CHANGELOG.md';
  const contentEl = document.getElementById('changelog-content');
  const modal = document.getElementById('changelog-modal');
  if (contentEl) contentEl.textContent = 'Loading changelog…';
  try {
    // Prefer raw GitHub content (fast and guaranteed when online)
    let res = await fetch(rawUrl);
    if (!res.ok) {
      // try local copy served by backend
      res = await fetch(localUrl);
    }
    if (res.ok) {
      const md = await res.text();
      if (contentEl) contentEl.textContent = md || 'No changelog available.';
    } else {
      // Fallback: open GitHub page in new tab
      window.open(repoBlob, '_blank', 'noopener');
      return;
    }
  } catch (e) {
    if (contentEl) contentEl.textContent = 'Unable to load changelog.';
    try { window.open(repoBlob, '_blank', 'noopener'); } catch (_) { }
  } finally {
    if (modal) modal.classList.remove('hidden');
    const gh = document.getElementById('changelog-github-link');
    if (gh) gh.href = repoBlob;
  }
}
