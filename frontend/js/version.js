// App version, update banner and release metadata.
//
// Part of the AmicoScript frontend. No build step: these are plain ES
// modules loaded directly by the browser via <script type="module">.

export async function fetchVersion() {
  const repoBase = 'https://github.com/sim186/AmicoScript';
  const rawVersionUrl = 'https://raw.githubusercontent.com/sim186/AmicoScript/main/VERSION';
  const remoteChangelog = `${repoBase}/blob/main/CHANGELOG.md`;
  const el = document.getElementById('app-version');
  const githubLinkEl = document.getElementById('github-link');
  const changelogRemoteEl = document.getElementById('changelog-remote-link');

  async function applyVersion(v) {
    const ver = (v || '').trim();
    if (el) el.textContent = ver || 'n/a';
    if (githubLinkEl) githubLinkEl.href = ver ? `${repoBase}/releases/tag/${ver.startsWith('v') ? ver : 'v' + ver}` : repoBase;
    if (changelogRemoteEl) changelogRemoteEl.href = remoteChangelog;
  }

  try {
    let v = '';
    // primary: API
    try {
      const res = await fetch('/api/version');
      if (res.ok) {
        const j = await res.json();
        v = (j.version || '').trim();
      }
    } catch (e) {
      // ignore api error, try fallbacks
    }

    // fallback: local static /VERSION
    if (!v) {
      try {
        const r2 = await fetch('/VERSION');
        if (r2.ok) v = (await r2.text()).trim();
      } catch (e) { }
    }

    // fallback: raw GitHub VERSION
    if (!v) {
      try {
        const r3 = await fetch(rawVersionUrl);
        if (r3.ok) v = (await r3.text()).trim();
      } catch (e) { }
    }

    await applyVersion(v);
  } catch (e) {
    await applyVersion('');
  }
}

export async function fetchLatestRelease() {
  try {
    const res = await fetch('/api/latest-release');
    if (!res.ok) return;
    const j = await res.json();
    const updateAvailable = !!j.update_available;
    const latest = j.latest || {};
    const local = j.local_version || '';
    const banner = document.getElementById('update-banner');
    const text = document.getElementById('update-banner-text');
    const link = document.getElementById('update-banner-link');
    const dismiss = document.getElementById('update-banner-dismiss');
    if (updateAvailable && banner && link && text) {
      const tag = latest.tag_name || '';
      const name = latest.name || tag || 'New release';
      const url = latest.html_url || (`https://github.com/sim186/AmicoScript/releases`);
      text.textContent = `Update available: ${name} (local: ${local || 'n/a'})`;
      link.href = url;
      link.textContent = 'View release';
      banner.classList.remove('hidden');
      if (dismiss) dismiss.addEventListener('click', () => banner.classList.add('hidden'));
    } else if (banner) {
      banner.classList.add('hidden');
    }
  } catch (e) {
    // ignore network errors
  }
}
