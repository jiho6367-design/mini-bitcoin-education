import { expect, test } from "./team-test";
import type { LabSnapshot } from "../src/api/types";

test("팀 모드 이동은 원본 영상과 서로 다른 실습 장부를 보존한다", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto("/");
  const video = page.getByTestId("team-demo-video");
  await expect(video).toBeVisible();
  await expect.poll(() => video.evaluate((element: HTMLVideoElement) => element.videoWidth)).toBe(2880);
  expect(await video.evaluate((element: HTMLVideoElement) => element.videoHeight)).toBe(1800);
  const duration = await video.evaluate((element: HTMLVideoElement) => element.duration);
  expect(duration).toBeGreaterThan(224);
  expect(duration).toBeLessThan(225);
  const media = await video.elementHandle();
  await video.evaluate(async (element: HTMLVideoElement) => {
    element.muted = true;
    await element.play();
  });
  const created = page.waitForResponse(response =>
    response.request().method() === "POST" && response.url().endsWith("/api/education/labs"));
  await page.getByRole("button", { name: /예제 실습 시작/ }).click();
  const { lab }: { lab: LabSnapshot } = await (await created).json();
  await expect(page.getByTestId("balance-0")).toHaveText("100코인");
  await expect(video).toHaveCount(0);
  expect(await media!.evaluate((element: HTMLVideoElement) => element.paused)).toBe(true);

  await page.getByRole("navigation", { name: "실습 모드" }).getByRole("link", { name: "기초 실습", exact: true }).click();
  await expect(page).toHaveURL("http://127.0.0.1:4173/basic");
  await expect(page.locator("#demo-video")).toBeVisible();
  await page.locator("#primary-nav").getByRole("link", { name: /지갑/ }).click();
  await page.getByTestId("wallet-name").fill("모드 검사 지갑");
  await page.getByTestId("wallet-create").click();
  await expect(page.locator("#wallet-list")).toContainText("모드 검사 지갑");
  const preserved = await page.request.get(`/api/education/labs/${lab.labId}`);
  expect(preserved.ok()).toBe(true);
  const { lab: unchanged }: { lab: LabSnapshot } = await preserved.json();
  expect(unchanged.wallets.map(wallet => wallet.name)).toEqual(["Alice", "Bob"]);
  expect(unchanged.nodes.A.balances[lab.wallets[0].address].confirmed).toBe(100);

  await page.getByRole("navigation", { name: "실습 모드" }).getByRole("link", { name: "통합 실습", exact: true }).click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("실습 안내");
  await page.getByRole("navigation", { name: "실습 모드" }).getByRole("link", { name: "공동 실습", exact: true }).click();
  await expect(page.getByRole("heading", { name: "공동 실습", exact: true })).toBeVisible();
  await page.getByRole("navigation", { name: "실습 모드" }).getByRole("link", { name: "통합 실습", exact: true }).click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("실습 안내");
  expect(errors).toEqual([]);
});
