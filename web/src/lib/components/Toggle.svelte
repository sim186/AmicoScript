<script lang="ts">
  let { checked, onchange, label, id }: { checked: boolean; onchange: (next: boolean) => void; label?: string; id?: string } =
    $props();
</script>

<button
  type="button"
  {id}
  role="switch"
  aria-checked={checked}
  aria-label={label}
  onclick={() => onchange(!checked)}
  class="relative inline-flex h-5 w-9 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition focus:outline-none focus:ring-2 focus:ring-brand {checked
    ? 'bg-brand'
    : 'bg-slate-200'}"
>
  <span
    class="pointer-events-none inline-block h-4 w-4 transform rounded-full bg-white shadow ring-0 transition"
    style="transform: translateX({checked ? '16px' : '0'})"
  ></span>
</button>
