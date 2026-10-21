export const authState = $state({
  enabled: true,
  passwordSet: false,
  loginRequired: false,
  local: true,
});

export const loginOverlay = $state({
  visible: false,
  message: "",
  formHidden: false,
});

export function showLoginOverlay(message?: string): void {
  loginOverlay.visible = true;
  if (message) loginOverlay.message = message;
}

export function hideLoginOverlay(): void {
  loginOverlay.visible = false;
}

export async function refreshAuthState() {
  try {
    const res = await fetch("/api/auth/status");
    if (res.ok) {
      const data = await res.json();
      authState.enabled = !!data.enabled;
      authState.passwordSet = !!data.password_set;
      authState.loginRequired = !!data.login_required;
      authState.local = !!data.local;
    }
  } catch {
    // server not reachable; leave the last known state
  }
  return authState;
}

export async function login(password: string): Promise<{ ok: boolean; error?: string }> {
  const fd = new FormData();
  fd.append("password", password);
  try {
    const res = await fetch("/api/auth/login", { method: "POST", body: fd });
    if (res.ok) {
      hideLoginOverlay();
      // Every panel fetched its data while the API was refusing requests, so
      // the page is showing nothing useful — reload rather than patch state.
      window.location.reload();
      return { ok: true };
    }
    const body = await res.json().catch(() => ({}));
    return { ok: false, error: body.detail || "Sign in failed." };
  } catch (err) {
    return { ok: false, error: (err as Error).message };
  }
}

export async function savePassword(
  newPassword: string,
  currentPassword: string,
): Promise<{ ok: boolean; error?: string; apiToken?: string }> {
  const fd = new FormData();
  fd.append("new_password", newPassword);
  fd.append("current_password", currentPassword);
  try {
    const res = await fetch("/api/auth/password", { method: "POST", body: fd });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) return { ok: false, error: body.detail || "Could not save the password." };
    await refreshAuthState();
    return { ok: true, apiToken: body.api_token };
  } catch (err) {
    return { ok: false, error: (err as Error).message };
  }
}

export async function removePassword(currentPassword: string): Promise<{ ok: boolean; error?: string }> {
  const params = new URLSearchParams({ current_password: currentPassword });
  try {
    const res = await fetch(`/api/auth/password?${params}`, { method: "DELETE" });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) return { ok: false, error: body.detail || "Could not remove the password." };
    await refreshAuthState();
    return { ok: true };
  } catch (err) {
    return { ok: false, error: (err as Error).message };
  }
}

export async function initAuth(): Promise<void> {
  const status = await refreshAuthState();
  if (status.enabled && status.loginRequired) {
    showLoginOverlay(
      status.passwordSet
        ? "Enter your password to continue."
        : "This AmicoScript has no password set, so it refuses connections from the " +
            "network. Set one from the app on the machine it runs on.",
    );
    if (!status.passwordSet) loginOverlay.formHidden = true;
  }
}
