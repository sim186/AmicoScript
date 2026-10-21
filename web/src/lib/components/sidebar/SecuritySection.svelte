<script lang="ts">
  import { authState, savePassword, removePassword } from "$lib/auth.svelte";

  let newPassword = $state("");
  let currentPassword = $state("");
  let feedback = $state("");
  let tone = $state<"info" | "ok" | "error">("info");
  let apiToken = $state("");
  let busy = $state(false);

  const stateLine = $derived(
    !authState.enabled
      ? "Authentication is disabled (AMICOSCRIPT_AUTH=off). Anything that can reach this server has full access."
      : authState.passwordSet
        ? "A password is set. Access from other machines requires signing in."
        : "No password set. This machine has full access; requests from the network are refused until you set one.",
  );

  async function onSave() {
    if (!newPassword) {
      feedback = "Enter a new password first.";
      tone = "error";
      return;
    }
    busy = true;
    const result = await savePassword(newPassword, currentPassword);
    busy = false;
    if (!result.ok) {
      feedback = result.error || "Could not save the password.";
      tone = "error";
      return;
    }
    newPassword = "";
    currentPassword = "";
    feedback = "Password saved.";
    tone = "ok";
    if (result.apiToken) apiToken = result.apiToken;
  }

  async function onRemove() {
    if (!confirm("Remove the password? Anyone who can reach this server will have full access.")) return;
    busy = true;
    const result = await removePassword(currentPassword);
    busy = false;
    if (!result.ok) {
      feedback = result.error || "Could not remove the password.";
      tone = "error";
      return;
    }
    currentPassword = "";
    feedback = "Password removed.";
    tone = "ok";
    apiToken = "";
  }
</script>

{#if authState.enabled}
  <div class="flex flex-col gap-3 text-xs">
    <p id="auth-state-line" class="text-ink-muted">{stateLine}</p>

    {#if authState.passwordSet}
      <div>
        <label for="auth-current-password" class="mb-1 block text-ink-muted">Current password</label>
        <input
          id="auth-current-password"
          type="password"
          bind:value={currentPassword}
          class="w-full rounded-lg border border-border px-2.5 py-1.5 text-xs focus:outline-none focus:ring-1 focus:ring-brand"
        />
      </div>
    {/if}

    <div>
      <label for="auth-new-password" class="mb-1 block text-ink-muted">{authState.passwordSet ? "New password" : "Set a password"}</label>
      <input
        id="auth-new-password"
        type="password"
        bind:value={newPassword}
        class="w-full rounded-lg border border-border px-2.5 py-1.5 text-xs focus:outline-none focus:ring-1 focus:ring-brand"
      />
    </div>

    <div class="flex items-center gap-2">
      <button
        id="auth-save-btn"
        onclick={onSave}
        disabled={busy}
        class="flex-1 rounded-md bg-brand px-2.5 py-1.5 text-xs font-medium text-white transition hover:bg-brand-hover focus:outline-none focus:ring-2 focus:ring-brand disabled:opacity-60"
      >
        {authState.passwordSet ? "Change password" : "Set password"}
      </button>
      {#if authState.passwordSet && authState.local}
        <button
          id="auth-remove-btn"
          onclick={onRemove}
          disabled={busy}
          class="rounded-md border border-border px-2.5 py-1.5 text-xs text-ink-muted transition hover:border-red-400 hover:text-red-400"
        >
          Remove
        </button>
      {/if}
    </div>

    {#if feedback}
      <p id="auth-feedback" class="{tone === 'error' ? 'text-red-400' : tone === 'ok' ? 'text-good' : 'text-ink-muted'}">{feedback}</p>
    {/if}

    {#if apiToken}
      <div id="auth-token-row">
        <label for="auth-api-token" class="mb-1 block text-ink-muted">API token (for TUI / scripts)</label>
        <input
          id="auth-api-token"
          type="text"
          readonly
          value={apiToken}
          class="w-full rounded-lg border border-border bg-surface-alt px-2.5 py-1.5 font-mono text-[10px] text-ink-muted"
        />
      </div>
    {/if}
  </div>
{/if}
