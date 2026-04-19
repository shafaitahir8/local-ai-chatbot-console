export type ThemePreference = "light" | "dark";

const THEME_STORAGE_KEY = "arctic_theme";

export function getStoredTheme(): ThemePreference {
  const stored = localStorage.getItem(THEME_STORAGE_KEY);
  return stored === "dark" ? "dark" : "light";
}

export function applyTheme(theme: ThemePreference) {
  document.documentElement.dataset.theme = theme;
  localStorage.setItem(THEME_STORAGE_KEY, theme);
  window.dispatchEvent(new CustomEvent("arctic-theme-changed", { detail: { theme } }));
}

export function initializeTheme() {
  applyTheme(getStoredTheme());
}
