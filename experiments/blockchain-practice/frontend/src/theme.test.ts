import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import packageInfo from "../package.json";
import packageLock from "../package-lock.json";
import {
  parseThemePreference,
  readThemePreference,
  resolveTheme,
  storeThemePreference,
  THEME_STORAGE_KEY,
  LEGACY_BASIC_THEME_STORAGE_KEY,
} from "./theme";

describe("theme preference", () => {
  it("defaults to system for absent or unsupported saved values", () => {
    for (const value of [null, undefined, "system", "unknown", "DARK", {}, 1]) {
      expect(parseThemePreference(value)).toBe("system");
    }
    expect(parseThemePreference("light")).toBe("light");
    expect(parseThemePreference("dark")).toBe("dark");
  });

  it("follows the operating system only for the system preference", () => {
    expect(resolveTheme("system", false)).toBe("light");
    expect(resolveTheme("system", true)).toBe("dark");
    expect(resolveTheme("light", true)).toBe("light");
    expect(resolveTheme("dark", false)).toBe("dark");
  });

  it("stores and restores only the theme preference under its dedicated key", () => {
    const values = new Map<string, string>();
    const storage = {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => {
        values.set(key, value);
      },
    };
    expect(readThemePreference(storage)).toBe("system");
    storeThemePreference("dark", storage);
    expect(readThemePreference(storage)).toBe("dark");
    storeThemePreference("system", storage);
    expect(readThemePreference(storage)).toBe("system");
    expect([...values]).toEqual([[THEME_STORAGE_KEY, "system"]]);
  });

  it("continues with system defaults when storage access is blocked", () => {
    const blocked = {
      getItem: () => {
        throw new Error("blocked storage");
      },
      setItem: () => {
        throw new Error("blocked storage");
      },
    };
    expect(readThemePreference(blocked)).toBe("system");
    expect(readThemePreference()).toBe("system");
    expect(() => storeThemePreference("dark", blocked)).not.toThrow();
    expect(() => storeThemePreference("dark")).not.toThrow();
  });

  it("migrates the earlier basic preference once without changing its old key", () => {
    for (const preference of ["system", "light", "dark"] as const) {
      const values = new Map<string, string>([[LEGACY_BASIC_THEME_STORAGE_KEY, preference]]);
      const storage = {
        getItem: (key: string) => values.get(key) ?? null,
        setItem: (key: string, value: string) => values.set(key, value),
      };
      expect(readThemePreference(storage)).toBe(preference);
      expect(values.get(THEME_STORAGE_KEY)).toBe(preference);
      expect(values.get(LEGACY_BASIC_THEME_STORAGE_KEY)).toBe(preference);
      storeThemePreference("light", storage);
      expect(readThemePreference(storage)).toBe("light");
      expect(values.get(LEGACY_BASIC_THEME_STORAGE_KEY)).toBe(preference);
    }
  });

  it("uses an existing shared choice even when an earlier basic choice differs", () => {
    for (const shared of ["system", "light", "dark", "invalid"]) {
      const reads: string[] = [];
      const storage = {
        getItem: (key: string) => {
          reads.push(key);
          return key === THEME_STORAGE_KEY ? shared : "dark";
        },
        setItem: () => { throw new Error("Existing shared values must not be rewritten"); },
      };
      expect(readThemePreference(storage)).toBe(parseThemePreference(shared));
      expect(reads).toEqual([THEME_STORAGE_KEY]);
    }
  });

  it("keeps a valid migrated choice in memory even when saving is blocked", () => {
    expect(readThemePreference({
      getItem: (key) => key === LEGACY_BASIC_THEME_STORAGE_KEY ? "dark" : null,
      setItem: () => { throw new Error("storage full"); },
    })).toBe("dark");
  });

  it("ignores invalid old values without creating a new saved preference", () => {
    const writes: string[] = [];
    expect(readThemePreference({
      getItem: (key) => key === LEGACY_BASIC_THEME_STORAGE_KEY ? "invalid" : null,
      setItem: (key) => { writes.push(key); },
    })).toBe("system");
    expect(writes).toEqual([]);
  });
});

it("keeps the lockfile project versions aligned with the UI's package source", () => {
  expect(packageLock.version).toBe(packageInfo.version);
  expect(packageLock.packages[""].version).toBe(packageInfo.version);
  expect(packageInfo.version).toMatch(/^\d+\.\d+\.\d+$/);
});

it("keeps the team Flask version aligned with the UI's package source", () => {
  const application = readFileSync(
    new URL("../../app.py", import.meta.url),
    "utf8",
  );
  expect(/^APP_VERSION = "(.+)"$/m.exec(application)?.[1]).toBe(packageInfo.version);
});
