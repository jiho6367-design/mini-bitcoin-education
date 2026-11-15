import { test, expect, type Page, type Route } from "@playwright/test";

type PracticeState = {
  practice: { id: string; revision: number };
  difficulty: number;
  nodes: { id: string; height: number; mining: boolean }[];
};

async function state(page: Page): Promise<PracticeState> {
  const response = await page.request.get("/api/state");
  expect(response.ok()).toBeTruthy();
  return response.json();
}

async function openSlowPractice(page: Page) {
  await page.goto("/");
  await expect
    .poll(async () =>
      (await page.context().cookies()).some(
        (cookie) => cookie.name === "mini_bitcoin_practice",
      ),
    )
    .toBeTruthy();
  await page.locator('nav [data-page="mining"]').click();
  await page.getByTestId("difficulty").fill("5");
  await page.getByTestId("difficulty-apply").click();
  await expect.poll(async () => (await state(page)).difficulty).toBe(5);
  await expect(page.getByTestId("mining-slow")).toBeEnabled();
}

for (const action of [
  { control: "practice-reset", endpoint: "/api/reset", changesPractice: false },
  {
    control: "practice-new",
    endpoint: "/api/practices/new",
    changesPractice: true,
  },
]) {
  test(`${action.control} confirmation queues safely behind a real pending mining response`, async ({
    page,
  }) => {
    const pageErrors: string[] = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));
    await openSlowPractice(page);
    const original = await state(page);

    let held = false;
    let release: () => void = () => undefined;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const pattern = "**/api/nodes/A/mine/step";
    const handler = async (route: Route) => {
      // Ordinary real batches proceed until the user has opened confirmation.
      // Then hold one genuine server response; no hash or response is fabricated.
      if (held || !(await page.locator("#confirm-dialog").isVisible())) {
        await route.continue();
        return;
      }
      const response = await route.fetch();
      held = true;
      await gate;
      await route.fulfill({ response });
    };
    await page.route(pattern, handler);
    try {
      // A legitimate early PoW success is allowed. Start another real candidate
      // if it finishes before confirmation can overlap a batch.
      for (let attempt = 0; attempt < 4 && !held; attempt++) {
        await expect(page.getByTestId("mining-slow")).toBeEnabled();
        await page.getByTestId("mining-slow").click();
        await page.getByTestId(action.control).click();
        await expect(page.locator("#confirm-dialog")).toBeVisible();
        await expect
          .poll(
            async () =>
              held ||
              !(await state(page)).nodes.find((n) => n.id === "A")!.mining,
          )
          .toBeTruthy();
        if (!held) await page.locator("#confirm-cancel").click();
      }
      expect(held).toBeTruthy();
      await expect(page.locator("#confirm-dialog")).toBeVisible();
      await test.info().attach(`${action.control}-during-pending-step`, {
        body: await page.screenshot(),
        contentType: "image/png",
      });
      const mutationResponse = page.waitForResponse(
        (response) =>
          new URL(response.url()).pathname === action.endpoint &&
          response.request().method() === "POST",
      );
      // Accept while the real calculation response is still withheld. The action
      // must remain queued rather than disappear because the request is busy.
      await page.locator("#confirm-accept").click();
      await expect(page.locator("#confirm-dialog")).not.toBeVisible();
      release();
      expect((await mutationResponse).ok()).toBeTruthy();
      await expect
        .poll(async () =>
          (await state(page)).nodes.every((n) => n.height === 0 && !n.mining),
        )
        .toBeTruthy();
      const final = await state(page);
      if (action.changesPractice)
        expect(final.practice.id).not.toBe(original.practice.id);
      else expect(final.practice.id).toBe(original.practice.id);
      await expect(page.locator("#task-message")).toBeHidden();
      const observationStarted = Date.now();
      await expect
        .poll(
          async () => {
            expect(
              (await state(page)).nodes.every(
                (n) => n.height === 0 && !n.mining,
              ),
            ).toBeTruthy();
            return Date.now() - observationStarted;
          },
          { intervals: [100, 200], timeout: 3000 },
        )
        .toBeGreaterThanOrEqual(650);
      expect(pageErrors).toEqual([]);
    } finally {
      release();
      await page.unroute(pattern, handler);
    }
  });
}
