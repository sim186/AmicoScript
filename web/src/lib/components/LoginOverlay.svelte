<script lang="ts">
  import { loginOverlay, login } from "$lib/auth.svelte";

  let password = $state("");
  let error = $state("");
  let submitting = $state(false);

  async function submit(e: Event) {
    e.preventDefault();
    submitting = true;
    error = "";
    const result = await login(password);
    submitting = false;
    if (!result.ok) {
      error = result.error || "Sign in failed.";
      password = "";
    }
  }
</script>

{#if loginOverlay.visible}
  <div class="fixed inset-0 z-50 flex items-center justify-center bg-canvas/60 backdrop-blur-sm">
    <div class="w-full max-w-sm rounded-2xl bg-surface p-6 shadow-xl">
      <h1 class="mb-1 text-lg font-semibold text-ink">AmicoScript</h1>
      <p id="login-message" class="mb-4 text-sm text-ink-muted">{loginOverlay.message}</p>

      {#if !loginOverlay.formHidden}
        <form onsubmit={submit} class="flex flex-col gap-3">
          <input
            id="login-password"
            type="password"
            autocomplete="current-password"
            placeholder="Password"
            bind:value={password}
            class="w-full rounded-lg border border-border px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand"
          />
          {#if error}
            <p class="text-xs text-red-400">{error}</p>
          {/if}
          <button
            type="submit"
            disabled={submitting}
            class="w-full rounded-lg bg-brand px-3 py-2 text-sm font-medium text-white transition hover:bg-brand-hover focus:outline-none focus:ring-2 focus:ring-brand disabled:opacity-60"
          >
            {submitting ? "Signing in…" : "Sign in"}
          </button>
        </form>
      {/if}
    </div>
  </div>
{/if}
