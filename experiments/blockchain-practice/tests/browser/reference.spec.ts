import { test, expect, type Page } from "@playwright/test";

async function openPractice(page: Page) {
  await page.goto("/basic");
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
  await expect(dictionary).toContainText("검색 결과가 없어요");
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
    "실습실의 네 계층",
  );
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await expect(page.getByText("SQLite 실습 저장")).toBeVisible();
  await expect(
    page.getByRole("heading", {
      name: "저장과 재시작",
      exact: true,
    }),
  ).toBeVisible();
  await expect(page.locator("main")).toContainText("누적 작업량");
  await expect(page.locator("main")).toContainText("실제 P2P 연결은 아니에요");

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
  await expect(page).toHaveURL(/\/basic#home$/);
  await expect(page.locator("#page-home")).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  expect(errors).toEqual([]);
});

test("the six-step overview navigates with the demo collapsed and the guide follows each learning page", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await openPractice(page);
  await expect(page.locator("#demo-video-panel")).toHaveJSProperty("open", false);
  await expect(page.locator("#demo-video")).toBeHidden();
  const overview = page.getByRole("article", { name: "전체 실습 안내", exact: true });
  await expect(overview).toBeVisible();
  const steps = overview.getByRole("link");
  await expect(steps).toHaveCount(6);

  for (const [index, destination] of [
    "wallet", "mining", "transaction", "mining", "chain", "network",
  ].entries()) {
    await steps.nth(index).click();
    await expect(page.locator(`#page-${destination}`)).toBeVisible();
    await expect(page.locator(`#page-${destination} #learning-guide`)).toBeVisible();
    if (destination === "mining") {
      await expect(page.locator("#guide-title")).toHaveText("Nonce와 해시");
    }
    await page.locator('nav [data-page="home"]').click();
    await expect(page.locator("#page-home #learning-guide")).toBeVisible();
    await expect(overview).toBeVisible();
  }
  expect(errors).toEqual([]);
});
