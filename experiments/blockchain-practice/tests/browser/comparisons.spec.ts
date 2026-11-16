import { test, expect, type Page } from "@playwright/test";

type NodeState = {
  id: string; height: number; tip_hash: string; valid: boolean;
  candidate: { job_id: string; attempts: number; samples: { nonce: number; hash: string }[] } | null;
};
type State = {
  nodes: NodeState[];
  mining_comparison: null | {
    comparison_id: string; status: string; winner: string | null; index?: number;
    candidates?: Record<string, { attempts: number }>;
  };
};
async function state(page: Page): Promise<State> {
  const response = await page.request.get("/api/state");
  expect(response.ok()).toBeTruthy();
  return response.json();
}
async function openMining(page: Page, difficulty: number) {
  await page.goto("/basic#mining");
  await expect(page.getByTestId("difficulty")).toBeEnabled();
  await page.getByTestId("difficulty").fill(String(difficulty));
  await page.getByTestId("difficulty-apply").click();
  await expect(page.getByTestId("difficulty-apply")).toBeEnabled();
}

test("single mining stays the default and three-node comparison can pause, step and stop", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await openMining(page, 5);
  await expect(page.locator("#single-mining-panel")).toBeVisible();
  await expect(page.locator("#comparison-mining-panel")).toBeHidden();
  await page.locator("#mining-view-compare").click();
  expect((await state(page)).mining_comparison).toBeNull();
  await page.getByTestId("comparison-start").click();
  await expect.poll(async () => (await state(page)).nodes.every((node) => (node.candidate?.attempts ?? 0) > 0)).toBeTruthy();
  await expect(page.locator("#mining-view-single")).toBeDisabled();
  await page.locator("#comparison-pause").click();
  await expect.poll(async () => (await state(page)).mining_comparison?.status).toBe("paused");
  const before = await state(page);
  await expect(page.locator("#comparison-guide")).toContainText("한 번씩 계산하거나 계속 비교");
  await page.waitForTimeout(600);
  const stationary = await state(page);
  expect(stationary.nodes.map((node) => node.candidate?.attempts)).toEqual(before.nodes.map((node) => node.candidate?.attempts));
  await page.locator("#comparison-step").click();
  await expect(page.locator("#comparison-step")).toBeEnabled();
  const after = await state(page);
  for (const node of after.nodes) {
    const previous = before.nodes.find((item) => item.id === node.id)!;
    expect(node.candidate!.attempts).toBe(previous.candidate!.attempts + 1);
    await expect(page.locator(`[data-comparison-node="${node.id}"] [data-comparison-attempts]`)).toHaveText(`${node.candidate!.attempts}회`);
    await expect(page.locator(`[data-comparison-node="${node.id}"] code`)).toHaveText(node.candidate!.samples.at(-1)!.hash);
  }
  await page.locator("#comparison-stop").click();
  await expect(page.locator("#mining-view-single")).toBeEnabled();
  expect((await state(page)).mining_comparison?.status).toBe("stopped");
  await page.locator("#mining-view-single").click();
  await expect(page.getByTestId("mining-step")).toBeVisible();
  expect(errors).toEqual([]);
});

test("comparison finds and delivers one block, preserves the result and fits mobile", async ({ page }, testInfo) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await openMining(page, 1);
  await page.locator("#mining-view-compare").click();
  await page.locator("#comparison-batch").selectOption("1000");
  await page.getByTestId("comparison-start").click();
  await expect(page.locator("#comparison-result-panel")).toBeVisible();
  const completed = await state(page);
  expect(completed.mining_comparison?.status).toBe("found");
  expect(new Set(completed.nodes.map((node) => node.tip_hash)).size).toBe(1);
  expect(completed.nodes.every((node) => node.height === 1 && node.valid)).toBeTruthy();
  await expect(page.locator("#comparison-result")).toContainText("블록 #1 발견");
  await expect(page.locator(".comparison-card.winner")).toHaveCount(1);
  await expect(page.locator("#comparison-result")).toContainText("검증·저장 완료");
  await page.reload();
  await expect(page.getByTestId("node-select")).toBeEnabled();
  await page.locator("#mining-view-compare").click();
  await expect(page.locator("#comparison-result-panel")).toBeVisible();
  expect((await state(page)).mining_comparison?.comparison_id).toBe(completed.mining_comparison?.comparison_id);
  await page.screenshot({ path: testInfo.outputPath("comparison-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  await expect(page.locator("[data-comparison-node='C']")).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("comparison-mobile.png"), fullPage: true });
  expect(errors).toEqual([]);
});

for (const action of ["start", "step", "resume"] as const) {
  test(`leaving the page during ${action} waits does not restart comparison on return`, async ({ page }) => {
    await openMining(page, 5);
    await page.locator("#mining-view-compare").click();
    if (action === "resume") {
      await page.locator("#comparison-step").click();
      await expect(page.locator("#comparison-resume")).toBeEnabled();
    }
    const steps: string[] = [];
    page.on("request", (request) => {
      if (request.method() === "POST" && request.url().endsWith("/api/mining/comparison/step")) steps.push(request.url());
    });
    let release!: () => void;
    const held = new Promise<void>((resolve) => { release = resolve; });
    let reached!: () => void;
    const entered = new Promise<void>((resolve) => { reached = resolve; });
    const endpoint = action === "resume" ? "resume" : "start";
    await page.route(`**/api/mining/comparison/${endpoint}`, async (route) => {
      const response = await route.fetch();
      reached();
      await held;
      await route.fulfill({ response });
    });
    await page.locator(`#comparison-${action}`).click();
    await entered;
    await page.evaluate(() => {
      window.dispatchEvent(new PageTransitionEvent("pagehide", { persisted: true }));
      window.dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true }));
    });
    release();
    await expect(page.locator("#comparison-resume")).toBeEnabled();
    await page.waitForTimeout(600);
    expect(steps).toEqual([]);
    await expect(page.locator("#comparison-guide")).toContainText("한 번씩 계산하거나 계속 비교");
  });
}
