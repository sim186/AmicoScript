export type SettingsSection = "general" | "ai" | "advanced";

export const settingsState = $state({
  open: false,
  section: "general" as SettingsSection,
});

export function openSettingsModal(section?: SettingsSection): void {
  if (section) settingsState.section = section;
  settingsState.open = true;
}

export function closeSettingsModal(): void {
  settingsState.open = false;
}
