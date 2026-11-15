import { test, expect, type Page } from "@playwright/test";

type Wallet = {
  name: string;
  address: string;
  balance: number;
  private_key?: string;
};
type LabState = {
  practice: { id: string; revision: number };
  nodes: {
    id: string;
    height: number;
    valid: boolean;
    mining: boolean;
    online: boolean;
  }[];
};

async function openPractice(page: Page) {
  await page.goto("/basic");
  await expect
    .poll(async () =>
      (await page.context().cookies()).some(
        (cookie) => cookie.name === "mini_bitcoin_practice",
      ),
    )
    .toBeTruthy();
}

async function state(page: Page): Promise<LabState> {
  const response = await page.request.get("/api/state");
  expect(response.ok()).toBeTruthy();
  return response.json();
}

async function wallets(page: Page): Promise<Wallet[]> {
  const response = await page.request.get("/api/wallets?node=A");
  expect(response.ok()).toBeTruthy();
  return response.json();
}

async function navigate(page: Page, section: string) {
  await page.locator(`nav [data-page="${section}"]`).click();
}

async function createWallet(page: Page, name: string) {
  await page.getByTestId("wallet-name").fill(name);
  await page.getByTestId("wallet-create").click();
  await expect
    .poll(async () => (await wallets(page)).some((w) => w.name === name))
    .toBeTruthy();
  await expect(page.getByTestId("wallet-name")).toHaveValue("");
}

async function mine(page: Page, height: number) {
  await navigate(page, "mining");
  await expect(page.getByTestId("mining-fast")).toBeEnabled();
  await page.getByTestId("mining-fast").click();
  await expect
    .poll(
      async () => (await state(page)).nodes.find((n) => n.id === "A")?.height,
    )
    .toBe(height);
  await expect(page.getByTestId("mining-fast")).toBeEnabled();
}

test("home plays the original captioned video and fits a narrow viewport", async ({
  page,
}, testInfo) => {
  await openPractice(page);
  const video = page.locator("video");
  await expect(video).toBeVisible();
  await expect
    .poll(() => video.evaluate((v: HTMLVideoElement) => v.videoWidth))
    .toBe(2880);
  expect(await video.evaluate((v: HTMLVideoElement) => v.videoHeight)).toBe(
    1800,
  );
  expect(await video.evaluate((v: HTMLVideoElement) => v.duration)).toBeCloseTo(
    224.2,
    0,
  );
  await video.evaluate((v: HTMLVideoElement) => v.play());
  await expect
    .poll(() => video.evaluate((v: HTMLVideoElement) => v.currentTime))
    .toBeGreaterThan(0.5);
  await video.evaluate((v: HTMLVideoElement) => v.pause());
  await page.screenshot({
    path: testInfo.outputPath("home-desktop.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  const bounds = await video.boundingBox();
  expect(bounds).not.toBeNull();
  expect(bounds!.x).toBeGreaterThanOrEqual(0);
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(391);
  await page.screenshot({
    path: testInfo.outputPath("home-mobile.png"),
    fullPage: true,
  });
});

test("wallet → invalidate changed draft → sign → mine updates all nodes", async ({
  page,
}, testInfo) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await openPractice(page);
  await navigate(page, "wallet");
  await createWallet(page, "Alice");
  await createWallet(page, "Bob");
  await navigate(page, "mining");
  await page.getByTestId("difficulty").fill("2");
  await page.getByTestId("difficulty-apply").click();
  await mine(page, 1);
  const funded = await wallets(page);
  const alice = funded.find((w) => w.name === "Alice")!;
  const bob = funded.find((w) => w.name === "Bob")!;
  expect([alice.balance, bob.balance]).toEqual([50, 50]);
  expect(funded.every((w) => w.private_key === undefined)).toBeTruthy();
  await navigate(page, "transaction");
  await page.getByTestId("tx-from").selectOption(alice.address);
  await page.getByTestId("tx-to").selectOption(bob.address);
  await page.getByTestId("tx-amount").fill("20");
  await page.getByTestId("tx-prepare").click();
  await expect(page.getByTestId("tx-sign")).toBeEnabled();
  await page.getByTestId("tx-amount").fill("21");
  await expect(page.getByTestId("tx-sign")).toBeDisabled();
  await expect(page.getByTestId("tx-submit")).toBeDisabled();
  await page.getByTestId("tx-amount").fill("20");
  await page.getByTestId("tx-prepare").click();
  await page.getByTestId("tx-sign").click();
  await expect(page.getByTestId("tx-submit")).toBeEnabled();
  await page.getByTestId("tx-submit").click();
  await expect(page.getByTestId("tx-submit")).toBeDisabled();
  await expect(page.getByTestId("tx-sign")).toBeDisabled();
  await mine(page, 2);
  const finished = await wallets(page);
  expect(finished.find((w) => w.name === "Alice")?.balance).toBe(30);
  expect(finished.find((w) => w.name === "Bob")?.balance).toBe(70);
  expect(
    (await state(page)).nodes.every((n) => n.height === 2 && n.valid),
  ).toBeTruthy();
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: testInfo.outputPath("mining-completed.png"),
    fullPage: true,
  });
  await navigate(page, "transaction");
  await expect(page.locator("#tx-stage")).toHaveText("블록 반영 완료");
  await expect(page.locator("#tx-result")).toContainText("블록 #2");
  await expect(page.locator("#tx-result")).toContainText("Node A 반영 완료");
  await expect(page.getByTestId("tx-sign")).toBeDisabled();
  await expect(page.getByTestId("tx-submit")).toBeDisabled();
  await navigate(page, "wallet");
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: testInfo.outputPath("wallet-balances.png"),
    fullPage: true,
  });

  await page.getByTestId("node-select").selectOption("B");
  await navigate(page, "chain");
  await page.getByTestId("tamper-block").selectOption("2");
  await page.getByTestId("tamper-tx").selectOption("1");
  await page.getByTestId("tamper-value").fill("1000");
  await page.getByTestId("tamper-mode").selectOption("simple");
  await page.getByTestId("tamper-start").click();
  await expect(page.locator("#confirm-dialog")).toBeVisible();
  await page.locator("#confirm-accept").click();
  await expect
    .poll(
      async () => (await state(page)).nodes.find((n) => n.id === "B")?.valid,
    )
    .toBe(false);
  expect(
    (await state(page)).nodes.filter((n) => n.id !== "B").every((n) => n.valid),
  ).toBeTruthy();
  await expect(page.locator("#integrity-warning")).toBeVisible();
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: testInfo.outputPath("tamper-detected.png"),
    fullPage: true,
  });
  await page.getByTestId("chain-sync").click();
  await expect
    .poll(async () => (await state(page)).nodes.every((n) => n.valid))
    .toBe(true);
  await expect(page.locator("#integrity-warning")).toBeHidden();
  expect(errors).toEqual([]);
});

test("an offline miner keeps its block local until other nodes synchronize", async ({
  page,
}) => {
  await openPractice(page);
  await navigate(page, "mining");
  await page.getByTestId("difficulty").fill("2");
  await page.getByTestId("difficulty-apply").click();
  await navigate(page, "network");
  await page.locator('[data-online="A"]').click();
  await expect
    .poll(
      async () => (await state(page)).nodes.find((n) => n.id === "A")?.online,
    )
    .toBe(false);
  await mine(page, 1);
  expect((await state(page)).nodes.map((n) => n.height)).toEqual([1, 0, 0]);
  await navigate(page, "network");
  await expect(page.getByTestId("node-sync-A")).toBeDisabled();
  await page.locator('[data-online="A"]').click();
  await expect
    .poll(
      async () => (await state(page)).nodes.find((n) => n.id === "A")?.online,
    )
    .toBe(true);
  for (const node of ["B", "C"]) {
    await page.getByTestId(`node-sync-${node}`).click();
    await expect
      .poll(
        async () =>
          (await state(page)).nodes.find((n) => n.id === node)?.height,
      )
      .toBe(1);
  }
  expect((await state(page)).nodes.every((n) => n.valid)).toBeTruthy();
});

test("browser practices are isolated and reload resumes the saved practice", async ({
  page,
  browser,
}) => {
  await openPractice(page);
  await navigate(page, "wallet");
  await createWallet(page, "Saved Alice");
  const original = await state(page);
  await page.reload();
  expect((await state(page)).practice.id).toBe(original.practice.id);
  expect(
    (await wallets(page)).some((w) => w.name === "Saved Alice"),
  ).toBeTruthy();
  const otherContext = await browser.newContext({
    baseURL: "http://127.0.0.1:5010",
  });
  try {
    const other = await otherContext.newPage();
    await openPractice(other);
    expect((await state(other)).practice.id).not.toBe(original.practice.id);
    expect(
      (await wallets(other)).some((w) => w.name === "Saved Alice"),
    ).toBeFalsy();
  } finally {
    await otherContext.close();
  }
});
