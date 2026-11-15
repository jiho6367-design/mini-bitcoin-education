import { test, expect, type Page } from "@playwright/test";

async function openPractice(page: Page) {
  await page.goto("/");
  await expect(page.getByTestId("node-select")).toBeEnabled();
}

test("the glossary opens, filters and closes entirely by keyboard", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await openPractice(page);

  const opener = page.locator("#glossary-open");
  await opener.focus();
  await opener.press("Enter");
  const dictionary = page.getByRole("dialog", { name: "블록체인 용어 사전" });
  await expect(dictionary).toBeVisible();
  const search = dictionary.getByRole("textbox", { name: "용어 검색" });
  await expect(search).toBeFocused();
  await search.fill("누적 작업량");
  await expect(dictionary.locator(".de")).toHaveCount(1);
  await expect(
    dictionary.getByText("동기화 · 누적 작업량", { exact: true }),
  ).toBeVisible();
  await expect(dictionary).toContainText("누적 작업량을 비교");

  await search.fill("찾을수없는용어123");
  await expect(dictionary).toContainText("검색 결과가 없습니다.");
  await search.press("Escape");
  await expect(dictionary).not.toBeVisible();
  await expect(opener).toBeFocused();
  expect(errors).toEqual([]);
});

test("the architecture page describes current behavior and inherits the chosen theme", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await openPractice(page);
  await page.getByLabel("화면 테마", { exact: true }).selectOption("dark");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.getByRole("link", { name: "구성도 ↗", exact: true }).click();
  await expect(page).toHaveURL(/\/architecture$/);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(
    "하나의 실습을 이루는 네 계층.",
  );
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await expect(page.getByText("SQLite 실습 저장")).toBeVisible();
  await expect(
    page.getByRole("heading", {
      name: "무엇이 저장되고, 무엇이 다시 시작되나요?",
      exact: true,
    }),
  ).toBeVisible();
  await expect(page.locator("main")).toContainText("누적 작업량");
  await expect(page.locator("main")).toContainText("실제 P2P 연결은 아닙니다");

  const dictionaryButton = page.getByRole("button", {
    name: "용어 사전",
    exact: true,
  });
  await dictionaryButton.focus();
  await dictionaryButton.press("Enter");
  await expect(
    page.getByRole("dialog", { name: "블록체인 용어 사전" }),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await page
    .getByRole("link", { name: "실습실로 돌아가기 →", exact: true })
    .click();
  await expect(page).toHaveURL(/\/#home$/);
  await expect(page.locator("#page-home")).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  expect(errors).toEqual([]);
});

test("leaving home pauses the live video and the guide follows each learning page", async ({
  page,
}) => {
  await openPractice(page);
  const video = page.locator("#demo-video");
  await expect
    .poll(() => video.evaluate((v: HTMLVideoElement) => v.readyState))
    .toBeGreaterThanOrEqual(2);
  await video.evaluate((v: HTMLVideoElement) => v.play());
  await expect
    .poll(() => video.evaluate((v: HTMLVideoElement) => v.currentTime))
    .toBeGreaterThan(0.2);

  await page.locator('nav [data-page="wallet"]').click();
  await expect(page.locator("#page-wallet #learning-guide")).toBeVisible();
  expect(await video.evaluate((v: HTMLVideoElement) => v.paused)).toBe(true);
  const pausedAt = await video.evaluate((v: HTMLVideoElement) => v.currentTime);

  await page.locator('nav [data-page="mining"]').click();
  await expect(page.locator("#page-mining #learning-guide")).toBeVisible();
  await expect(page.locator("#guide-title")).toHaveText(
    "Nonce 하나로 해시가 달라집니다.",
  );
  await page.locator('nav [data-page="home"]').click();
  await expect(page.locator("#page-home #learning-guide")).toBeVisible();
  expect(await video.evaluate((v: HTMLVideoElement) => v.paused)).toBe(true);
  expect(
    await video.evaluate((v: HTMLVideoElement) => v.currentTime),
  ).toBeCloseTo(pausedAt, 2);
});
