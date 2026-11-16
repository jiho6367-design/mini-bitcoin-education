import { expect, test, type Page } from "./team-test";

type Mode = "basic" | "classroom";
type Theme = "light" | "dark";

async function ready(page: Page, mode: Mode) {
  if (mode === "basic") {
    await expect(page.getByTestId("node-select")).toBeEnabled();
  } else {
    await expect(page.getByRole("heading", { name: "공동 실습", exact: true })).toBeVisible();
  }
  const modes = page.getByRole("navigation", { name: "실습 모드", exact: true });
  await expect(modes.getByRole("link")).toHaveText(["기초 실습", "공동 실습", "넌스 찾기"]);
  await expect(page.getByLabel("화면 테마", { exact: true })).toBeEnabled();
}

async function move(page: Page, mode: Mode) {
  await page.getByRole("navigation", { name: "실습 모드", exact: true })
    .getByRole("link", { name: mode === "basic" ? "기초 실습" : "공동 실습", exact: true }).click();
  await ready(page, mode);
}

async function appearance(page: Page, mode: Mode) {
  return page.evaluate((currentMode) => {
    const find = (selector: string) => {
      const element = document.querySelector<HTMLElement>(selector);
      if (!element) throw new Error(`Missing visible theme target: ${selector}`);
      return element;
    };
    const rgb = (value: string): [number, number, number, number] => {
      const numbers = value.match(/[\d.]+/g)?.map(Number);
      if (!numbers || numbers.length < 3) throw new Error(`Unsupported computed color ${value}`);
      return [numbers[0], numbers[1], numbers[2], numbers[3] ?? 1];
    };
    const background = (element: Element | null): [number, number, number] => {
      if (!element) return [255, 255, 255];
      const [r, g, b, alpha] = rgb(getComputedStyle(element).backgroundColor);
      if (alpha === 1) return [r, g, b];
      const behind = background(element.parentElement);
      return [r * alpha + behind[0] * (1 - alpha), g * alpha + behind[1] * (1 - alpha),
        b * alpha + behind[2] * (1 - alpha)];
    };
    const luminance = (channels: number[]) => {
      const linear = channels.map(channel => {
        const value = channel / 255;
        return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
      });
      return linear[0] * 0.2126 + linear[1] * 0.7152 + linear[2] * 0.0722;
    };
    const ratio = (foreground: number[], behind: number[]) => {
      const a = luminance(foreground), b = luminance(behind);
      return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
    };
    const surface = find(currentMode === "basic" ? "#page-home .panel" : "#classroom-main .panel");
    const title = find(currentMode === "basic" ? "#page-title" : "#classroom-main .panel h2");
    const muted = find(currentMode === "basic" ? "#page-description" : ".team-entry-footnote");
    const selected = find(currentMode === "basic"
      ? '.mode-switch a[aria-current="page"]' : '.team-navigation a[aria-current="page"]');
    const root = getComputedStyle(document.documentElement);
    // Vite may shorten #ffffff to #fff; compare their color values.
    const normalized = (value: string) => value.trim().toLowerCase()
      .replace(/^#([\da-f])([\da-f])([\da-f])$/, (_match, r: string, g: string, b: string) =>
        `#${r}${r}${g}${g}${b}${b}`);
    const tokens = Object.fromEntries([
      "--page-bg", "--surface", "--text", "--muted", "--border", "--accent", "--selected-bg",
    ].map(name => [name, normalized(root.getPropertyValue(name))]));
    const buttons = Array.from(document.querySelectorAll<HTMLButtonElement>("button"))
      .filter(button => button.getClientRects().length > 0 && !button.matches(":disabled")
        && getComputedStyle(button).visibility !== "hidden")
      .map(button => {
        const style = getComputedStyle(button);
        const color = rgb(style.color);
        const behind = background(button);
        const foreground = color.slice(0, 3).map((value, index) =>
          value * color[3] + behind[index] * (1 - color[3]));
        return { label: button.textContent?.trim(), contrast: ratio(foreground, behind) };
      });
    return {
      resolved: document.documentElement.dataset.theme,
      preference: document.documentElement.dataset.themePreference,
      tokens,
      colors: {
        background: background(document.body),
        surface: getComputedStyle(surface).backgroundColor,
        text: getComputedStyle(title).color,
        muted: getComputedStyle(muted).color,
        border: getComputedStyle(surface).borderTopColor,
        accent: getComputedStyle(selected).color,
        selected: getComputedStyle(selected).backgroundColor,
      },
      buttons,
      overflow: document.documentElement.scrollWidth > innerWidth,
    };
  }, mode);
}

async function inspect(page: Page, mode: Mode, preference: string, resolved: Theme) {
  await expect(page.locator("html")).toHaveAttribute("data-theme", resolved);
  await expect(page.locator("html")).toHaveAttribute("data-theme-preference", preference);
  await expect(page.getByLabel("화면 테마", { exact: true })).toHaveValue(preference);
  const value = await appearance(page, mode);
  expect(value.overflow, `${mode}: 390px horizontal overflow`).toBe(false);
  expect(value.buttons.length).toBeGreaterThan(0);
  for (const button of value.buttons) {
    expect(button.contrast, `${mode}: readable button ${button.label}`).toBeGreaterThanOrEqual(4.5);
  }
  return value;
}

for (const preference of ["light", "dark"] as const) {
  test(`${preference} 선택은 기초·공동 왕복에서 실제 색과 읽기 대비를 유지한다`, async ({ page }, info) => {
    const mutations: string[] = [];
    page.on("request", request => {
      if (request.url().includes("/api/") && !["GET", "HEAD"].includes(request.method())) mutations.push(request.url());
    });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.emulateMedia({ colorScheme: preference === "light" ? "dark" : "light", reducedMotion: "reduce" });
    await page.goto("/");
    await ready(page, "basic");
    await page.getByLabel("화면 테마", { exact: true }).selectOption(preference);
    const basic = await inspect(page, "basic", preference, preference);
    await page.screenshot({ path: info.outputPath(`basic-${preference}-390.png`), fullPage: true });
    await move(page, "classroom");
    const classroom = await inspect(page, "classroom", preference, preference);
    expect(classroom.tokens).toEqual(basic.tokens);
    expect(classroom.colors).toEqual(basic.colors);
    await page.screenshot({ path: info.outputPath(`classroom-${preference}-390.png`), fullPage: true });
    await move(page, "basic");
    const returned = await inspect(page, "basic", preference, preference);
    expect(returned.tokens).toEqual(basic.tokens);
    expect(returned.colors).toEqual(basic.colors);
    expect(mutations).toEqual([]);
  });
}

test("시스템 모드는 OS 변경과 기초·공동 이동을 함께 따라가며 실습을 실행하지 않는다", async ({ page }, info) => {
  const mutations: string[] = [];
  page.on("request", request => {
    if (request.url().includes("/api/") && !["GET", "HEAD"].includes(request.method())) mutations.push(request.url());
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ colorScheme: "light", reducedMotion: "reduce" });
  await page.goto("/");
  await ready(page, "basic");
  await page.getByLabel("화면 테마", { exact: true }).selectOption("system");
  const basicLight = await inspect(page, "basic", "system", "light");
  await move(page, "classroom");
  const classroomLight = await inspect(page, "classroom", "system", "light");
  expect(classroomLight.tokens).toEqual(basicLight.tokens);
  expect(classroomLight.colors).toEqual(basicLight.colors);
  await page.emulateMedia({ colorScheme: "dark", reducedMotion: "reduce" });
  const classroomDark = await inspect(page, "classroom", "system", "dark");
  await page.screenshot({ path: info.outputPath("classroom-system-dark-390.png"), fullPage: true });
  await move(page, "basic");
  const basicDark = await inspect(page, "basic", "system", "dark");
  expect(basicDark.tokens).toEqual(classroomDark.tokens);
  expect(basicDark.colors).toEqual(classroomDark.colors);
  await page.emulateMedia({ colorScheme: "light", reducedMotion: "reduce" });
  const restoredLight = await inspect(page, "basic", "system", "light");
  expect(restoredLight.colors).toEqual(basicLight.colors);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.screenshot({ path: info.outputPath("basic-system-light-1440.png"), fullPage: true });
  expect(mutations).toEqual([]);
});
