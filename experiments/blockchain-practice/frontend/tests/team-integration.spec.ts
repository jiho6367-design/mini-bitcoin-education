import { expect, test } from "./team-test";
import type { LabSnapshot } from "../src/api/types";

for (const viewport of [
  { name: "컴퓨터", width: 1440, height: 1000 },
  { name: "휴대폰", width: 390, height: 844 },
]) {
  test(`${viewport.name}에서 기초를 바로 열고 기존 공동 링크를 왕복해도 장부가 유지된다`, async ({ page }, testInfo) => {
    const errors: string[] = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await page.goto("/");
    await expect(page.getByTestId("node-select")).toBeEnabled();
    const modes = page.getByRole("navigation", { name: "실습 모드", exact: true });
    await expect(modes.getByRole("link")).toHaveText(["기초 실습", "공동 실습", "넌스 찾기"]);
    await expect(modes.getByRole("link", { name: "기초 실습", exact: true })).toHaveAttribute("aria-current", "page");
    await expect(page.locator("#demo-video-panel")).toHaveJSProperty("open", false);
    await expect(page.locator("#demo-video")).toBeHidden();
    const overview = page.getByRole("article", { name: "전체 실습 안내", exact: true });
    await expect(overview).toBeVisible();
    await expect(overview.getByRole("listitem")).toHaveCount(6);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: testInfo.outputPath(`basic-main-${viewport.width}.png`) });

    await overview.getByRole("link", { name: "지갑 만들기 →", exact: true }).click();
    await expect(page.locator("#page-wallet #learning-guide")).toBeVisible();
    const walletName = `${viewport.name} 이동 검사 지갑`;
    await page.getByTestId("wallet-name").fill(walletName);
    await page.getByTestId("wallet-create").click();
    await expect(page.locator("#wallet-list")).toContainText(walletName);
    const first = await (await page.request.get("/api/state")).json();
    const practiceCookie = (await page.context().cookies()).find(cookie => cookie.name === "mini_bitcoin_practice")!.value;

    // Old basic bookmarks and the new main URL open the same browser ledger.
    for (const path of ["/basic", "/"]) {
      await page.goto(path);
      await expect(page.getByTestId("node-select")).toBeEnabled();
      const state = await (await page.request.get("/api/state")).json();
      expect(state.practice.id).toBe(first.practice.id);
      expect(state.revision).toBe(first.revision);
      const wallets = await (await page.request.get("/api/wallets?node=A")).json();
      expect(wallets.some((wallet: { name: string }) => wallet.name === walletName)).toBe(true);
    }

    // Use the unchanged legacy query format with a real saved team code.
    const prepared = await page.request.post("/api/education/team/rooms", {
      data: { name: `${viewport.name} 참가자` },
    });
    expect(prepared.ok()).toBe(true);
    const credential = await prepared.json();
    const headers = { Authorization: `Bearer ${credential.token}` };
    const beforeRoom = await (await page.request.get(`/api/education/classroom/rooms/${credential.code}`, { headers })).json();
    await modes.getByRole("link", { name: "공동 실습", exact: true }).click();
    await expect(page.getByRole("heading", { name: "공동 실습", exact: true })).toBeVisible();
    await page.goto(`/?classroom=join&room=${credential.code}`);
    await page.getByLabel("별명", { exact: true }).fill(`${viewport.name} 참가자`);
    await page.locator("form").getByRole("button", { name: "기존 실습에 입장", exact: true }).click();
    await expect(page.getByText("연결됨", { exact: true })).toBeVisible();
    await expect(page.getByTestId("team-identity")).toContainText(`${viewport.name} 참가자`);
    await expect(modes.getByRole("link")).toHaveText(["기초 실습", "공동 실습", "넌스 찾기"]);
    await expect(modes.getByRole("link", { name: "공동 실습", exact: true })).toHaveAttribute("aria-current", "page");
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: testInfo.outputPath(`classroom-main-${viewport.width}.png`) });
    const afterRoom = await (await page.request.get(`/api/education/classroom/rooms/${credential.code}`, { headers })).json();
    expect(afterRoom.lab.nodes).toEqual(beforeRoom.lab.nodes);

    await modes.getByRole("link", { name: "기초 실습", exact: true }).click();
    await expect(page).toHaveURL(/\/$/);
    await expect(page.getByTestId("node-select")).toBeEnabled();
    const restored = await (await page.request.get("/api/state")).json();
    expect(restored.practice.id).toBe(first.practice.id);
    expect(restored.revision).toBe(first.revision);
    expect((await page.context().cookies()).find(cookie => cookie.name === "mini_bitcoin_practice")!.value).toBe(practiceCookie);
    expect(errors).toEqual([]);
  });
}

test("숨겨진 이전 통합 실습과 보관함은 기초 장부와 분리해 보존한다", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto("/advanced");
  const created = page.waitForResponse(response =>
    response.request().method() === "POST" && response.url().endsWith("/api/education/labs"));
  await page.getByRole("button", { name: /예제 실습 시작/ }).click();
  const { lab }: { lab: LabSnapshot } = await (await created).json();
  await expect(page.getByTestId("balance-0")).toHaveText("100코인");
  const title = `이전 기록 ${lab.storage!.id.slice(0, 8)}`;
  const renamed = await page.request.post(`/api/education/practices/${lab.storage!.id}/rename`, { data: { title } });
  expect(renamed.ok()).toBe(true);
  const owner = (await page.context().cookies()).find(cookie => cookie.name === "team_education_owner")!.value;

  await page.getByRole("navigation", { name: "실습 모드" }).getByRole("link", { name: "기초 실습", exact: true }).click();
  await expect(page.getByTestId("node-select")).toBeEnabled();
  await page.locator("#primary-nav").getByRole("link", { name: /지갑/ }).click();
  await page.getByTestId("wallet-name").fill("이전 기록과 별도 지갑");
  await page.getByTestId("wallet-create").click();
  await expect(page.locator("#wallet-list")).toContainText("이전 기록과 별도 지갑");
  const basic = await (await page.request.get("/api/state")).json();
  const { lab: unchanged }: { lab: LabSnapshot } = await (await page.request.get(`/api/education/labs/${lab.labId}`)).json();
  expect(unchanged.wallets.map(wallet => wallet.name)).toEqual(["Alice", "Bob"]);
  expect(unchanged.nodes.A.balances[lab.wallets[0].address].confirmed).toBe(100);
  await expect(page.getByRole("navigation", { name: "실습 모드" }).getByRole("link", { name: "통합 실습", exact: true })).toHaveCount(0);

  await page.goto("/advanced");
  await page.getByRole("button", { name: "저장된 실습", exact: true }).click();
  await page.locator(".archive-item").filter({ hasText: title }).getByRole("button", { name: "이어 하기", exact: true }).click();
  await expect(page.getByTestId("balance-0")).toHaveText("100코인");
  expect((await page.context().cookies()).find(cookie => cookie.name === "team_education_owner")!.value).toBe(owner);
  const preservedBasic = await (await page.request.get("/api/state")).json();
  expect(preservedBasic.practice.id).toBe(basic.practice.id);
  expect(preservedBasic.revision).toBe(basic.revision);
  expect(errors).toEqual([]);
});
