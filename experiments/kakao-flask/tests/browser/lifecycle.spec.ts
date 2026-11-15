import { test, expect, type Page } from "@playwright/test";

type Candidate = {
  job_id: string;
  status: string;
  attempts: number;
  nonce: number;
  hash: string;
};
type State = {
  practice: { id: string };
  nodes: {
    id: string;
    mining: boolean;
    height: number;
    candidate: Candidate | null;
  }[];
};

async function state(page: Page): Promise<State> {
  const response = await page.request.get("/api/state");
  expect(response.ok()).toBeTruthy();
  return response.json();
}
async function open(page: Page) {
  await page.goto("/");
  await expect
    .poll(async () =>
      (await page.context().cookies()).some(
        (cookie) => cookie.name === "mini_bitcoin_practice",
      ),
    )
    .toBe(true);
  await expect(page.getByTestId("practice-reset")).toBeEnabled();
}
async function navigate(page: Page, section: string) {
  await page.locator(`nav [data-page="${section}"]`).click();
}
async function post(page: Page, path: string, data = {}) {
  const response = await page.request.post(path, { data });
  expect(response.ok()).toBeTruthy();
  return response.json();
}
async function fundedWallets(page: Page) {
  const alice = await post(page, "/api/wallets", { name: "Reset Alice" });
  const bob = await post(page, "/api/wallets", { name: "Reset Bob" });
  await post(page, "/api/difficulty", { difficulty: 1 });
  const job = await post(page, "/api/nodes/A/mine/start");
  for (let i = 0; i < 100; i++) {
    const result = await post(page, "/api/nodes/A/mine/step", {
      job_id: job.job_id,
      batch: 1000,
    });
    if (result.found) {
      await page.reload();
      await expect(page.getByTestId("practice-reset")).toBeEnabled();
      return { alice: alice.address, bob: bob.address };
    }
  }
  throw new Error(
    "The actual funding block did not satisfy difficulty 1 in 100000 attempts",
  );
}

test("single hash, slow run, pause, resume and stop follow the server job", async ({
  page,
}) => {
  await open(page);
  await navigate(page, "mining");
  await page.getByTestId("difficulty").fill("5");
  await page.getByTestId("difficulty-apply").click();

  let candidate: Candidate | null = null;
  // A real first hash may legitimately win. Start another real job in that rare case.
  for (let i = 0; i < 5; i++) {
    await page.getByTestId("mining-step").click();
    await expect(page.getByTestId("mining-step")).toBeEnabled();
    candidate = (await state(page)).nodes[0].candidate;
    if (candidate?.status === "paused") break;
  }
  expect(candidate?.status).toBe("paused");
  expect(candidate?.attempts).toBe(1);
  await expect(page.getByTestId("mining-nonce")).toHaveText(
    String(candidate!.nonce),
  );
  await expect(page.getByTestId("mining-hash")).toHaveText(candidate!.hash);
  const jobId = candidate!.job_id;
  await page.getByTestId("mining-resume").click();
  await expect
    .poll(async () => (await state(page)).nodes[0].candidate?.attempts ?? 0)
    .toBeGreaterThan(1);
  await page.getByTestId("mining-pause").click();
  await expect(page.getByTestId("mining-resume")).toBeEnabled();
  const paused = (await state(page)).nodes[0].candidate!;
  expect(paused.status).toBe("paused");
  await page.waitForTimeout(400);
  expect((await state(page)).nodes[0].candidate?.attempts).toBe(
    paused.attempts,
  );
  await page.getByTestId("mining-resume").click();
  await expect
    .poll(async () => (await state(page)).nodes[0].candidate?.attempts ?? 0)
    .toBeGreaterThan(paused.attempts);
  await page.getByTestId("mining-stop").click();
  await expect(page.getByTestId("mining-fast")).toBeEnabled();
  const stopped = (await state(page)).nodes[0];
  expect(stopped.mining).toBe(false);
  expect(stopped.candidate?.status).toBe("stopped");
  await page.getByTestId("mining-slow").click();
  await expect
    .poll(async () => (await state(page)).nodes[0].candidate?.job_id)
    .not.toBe(jobId);
  await page.getByTestId("mining-stop").click();
  await expect(page.getByTestId("mining-step")).toBeEnabled();
});

test("explicit key inspection closes cleanly and reset invalidates a signed draft", async ({
  page,
}) => {
  await open(page);
  const { alice, bob } = await fundedWallets(page);
  const practiceId = (await state(page)).practice.id;
  await navigate(page, "wallet");
  await page.locator(`[data-keys="${alice}"]`).click();
  await expect(page.locator("#keys-dialog")).toBeVisible();
  await expect(page.locator("#wallet-private-key")).toHaveText(
    /^[0-9a-f]{64}$/,
  );
  await page.locator("#keys-close").click();
  await expect(page.locator("#wallet-private-key")).toHaveText("");
  await navigate(page, "transaction");
  await page.getByTestId("tx-from").selectOption(alice);
  await page.getByTestId("tx-to").selectOption(bob);
  await page.getByTestId("tx-amount").fill("10");
  await page.getByTestId("tx-prepare").click();
  await page.getByTestId("tx-sign").click();
  await expect(page.getByTestId("tx-submit")).toBeEnabled();
  await page.getByTestId("practice-reset").click();
  await page.locator("#confirm-accept").click();
  await expect(page.getByTestId("tx-submit")).toBeDisabled();
  await expect(page.getByTestId("tx-sign")).toBeDisabled();
  await expect
    .poll(async () => (await state(page)).nodes.every((n) => n.height === 0))
    .toBe(true);
  expect((await state(page)).practice.id).toBe(practiceId);
  const response = await page.request.get("/api/wallets");
  expect(
    (await response.json()).some(
      (w: { address: string }) => w.address === alice,
    ),
  ).toBe(false);
});

test("dark theme persists and every learning page fits a phone viewport", async ({
  page,
}, testInfo) => {
  await open(page);
  await fundedWallets(page);
  await page.locator("#theme-select").selectOption("dark");
  const practiceId = (await state(page)).practice.id;
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  expect((await state(page)).practice.id).toBe(practiceId);
  await navigate(page, "wallet");
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: testInfo.outputPath("wallet-dark.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  for (const section of [
    "wallet",
    "transaction",
    "mempool",
    "mining",
    "chain",
    "network",
    "events",
    "home",
  ]) {
    await navigate(page, section);
    await expect(page.locator("#learning-guide")).toBeVisible();
    expect(
      await page.evaluate(
        () =>
          document.documentElement.scrollWidth <=
          document.documentElement.clientWidth,
      ),
    ).toBe(true);
  }
});
