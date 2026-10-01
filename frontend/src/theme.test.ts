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
});

it("keeps the lockfile project versions aligned with the UI's package source", () => {
  expect(packageLock.version).toBe(packageInfo.version);
  expect(packageLock.packages[""].version).toBe(packageInfo.version);
  expect(packageInfo.version).toMatch(/^\d+\.\d+\.\d+$/);
});

it("keeps the backend project version aligned with the UI's package source", () => {
  const pyproject = readFileSync(
    new URL("../../backend/pyproject.toml", import.meta.url),
    "utf8",
  );
  expect(/^version = "(.+)"$/m.exec(pyproject)?.[1]).toBe(packageInfo.version);
});
