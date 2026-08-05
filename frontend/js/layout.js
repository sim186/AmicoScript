// Sidebar show/hide, including the mobile overlay.
//
// Part of the AmicoScript frontend. No build step: these are plain ES
// modules loaded directly by the browser via <script type="module">.

export function isMobile() { return window.innerWidth < 768; }

export function openDrawer() {
  const sb = document.getElementById('sidebar'); if (!sb) return;
  if (isMobile()) {
    sb.classList.add('mobile-open');
    const bd = document.getElementById('sidebar-backdrop');
    if (bd) bd.classList.add('active');
  } else {
    sb.classList.remove('hidden');
    document.documentElement.style.setProperty('--console-left', '18rem');
  }
  localStorage.setItem('drawerOpen', '1');
}

export function closeDrawer() {
  const sb = document.getElementById('sidebar'); if (!sb) return;
  if (isMobile()) {
    sb.classList.remove('mobile-open');
    const bd = document.getElementById('sidebar-backdrop');
    if (bd) bd.classList.remove('active');
  } else {
    sb.classList.add('hidden');
    document.documentElement.style.setProperty('--console-left', '0');
  }
  localStorage.setItem('drawerOpen', '0');
}

export function toggleDrawer() {
  const sb = document.getElementById('sidebar'); if (!sb) return;
  const isOpen = isMobile() ? sb.classList.contains('mobile-open') : !sb.classList.contains('hidden');
  if (isOpen) closeDrawer(); else openDrawer();
}

window.addEventListener('resize', () => {
  const sb = document.getElementById('sidebar'); if (!sb) return;
  const bd = document.getElementById('sidebar-backdrop');
  if (!isMobile()) {
    // Switching to desktop: remove mobile classes, restore display
    sb.classList.remove('mobile-open');
    if (bd) bd.classList.remove('active');
    // Restore desktop open/close from localStorage
    if (localStorage.getItem('drawerOpen') === '0') {
      sb.classList.add('hidden');
      document.documentElement.style.setProperty('--console-left', '0');
    } else {
      sb.classList.remove('hidden');
      document.documentElement.style.setProperty('--console-left', '18rem');
    }
  } else {
    // Switching to mobile: remove desktop hidden class (CSS takes over positioning)
    sb.classList.remove('hidden');
    document.documentElement.style.setProperty('--console-left', '0');
  }
});
