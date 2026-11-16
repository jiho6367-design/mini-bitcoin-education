export type ThemePreference = "system" | "light" | "dark";
export type ResolvedTheme = "light" | "dark";

export const THEME_STORAGE_KEY = "team-blockchain-theme";
export const LEGACY_BASIC_THEME_STORAGE_KEY = "blockchain-lab-theme";

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
    const stored = storage?.getItem(THEME_STORAGE_KEY);
    if (stored != null) return parseThemePreference(stored);
    const legacy = storage?.getItem(LEGACY_BASIC_THEME_STORAGE_KEY);
    if (legacy === "light" || legacy === "dark" || legacy === "system") {
      // Read the old basic choice only once; keep the original key untouched.
      storeThemePreference(legacy, storage);
      return legacy;
    }
    return "system";
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
