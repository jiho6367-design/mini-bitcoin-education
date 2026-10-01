export type ThemePreference = "system" | "light" | "dark";
export type ResolvedTheme = "light" | "dark";

export const THEME_STORAGE_KEY = "mini-bitcoin-theme";

type ThemeStorage = Pick<Storage, "getItem" | "setItem">;

export function parseThemePreference(value: unknown): ThemePreference {
  return value === "light" || value === "dark" ? value : "system";
}

export function resolveTheme(
  preference: ThemePreference,
  systemDark: boolean,
): ResolvedTheme {
  return preference === "system" ? (systemDark ? "dark" : "light") : preference;
}

export function readThemePreference(storage?: ThemeStorage): ThemePreference {
  try {
    return parseThemePreference(storage?.getItem(THEME_STORAGE_KEY));
  } catch {
    return "system";
  }
}

export function storeThemePreference(
  preference: ThemePreference,
  storage?: ThemeStorage,
): void {
  try {
    storage?.setItem(THEME_STORAGE_KEY, preference);
  } catch {
    // Storage can be unavailable; the current tab still follows the selection.
  }
}

function browserStorage(): ThemeStorage | undefined {
  try {
    return window.localStorage;
  } catch {
    return undefined;
  }
}

export function loadBrowserTheme(): ThemePreference {
  return readThemePreference(browserStorage());
}

export function saveBrowserTheme(preference: ThemePreference): void {
  storeThemePreference(preference, browserStorage());
}

export function applyTheme(
  preference: ThemePreference,
  systemDark: boolean,
): void {
  document.documentElement.dataset.theme = resolveTheme(preference, systemDark);
  document.documentElement.dataset.themePreference = preference;
}

export function initializeTheme(): void {
  applyTheme(
    loadBrowserTheme(),
    window.matchMedia("(prefers-color-scheme: dark)").matches,
  );
}
