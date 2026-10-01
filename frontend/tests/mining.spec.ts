import { expect, test, type Page } from "@playwright/test";

// 채굴 규칙 자체(단계·일시정지·표본·시간 집계)는 백엔드의 pytest가 검사한다.
// 여기서는 화면이 백엔드의 채굴 작업을 올바르게 요청하고 표시하는지 확인한다.
const navigate = (page: Page, title: string) =>
  page
    .getByRole("navigation")
    .getByRole("button", { name: new RegExp(title) })
    .click();

// 시작 화면에서 실습을 시작해 대기 거래 하나를 만들고 채굴 화면으로 이동한다.
async function startAndSubmit(page: Page, difficulty: string) {
  await page
    .getByLabel("실습 난이도", { exact: true })
    .selectOption(difficulty);
  await page.getByRole("button", { name: /^예제 실습 시작/ }).click();
  for (const name of ["1. 거래 작성", "2. 전자서명", "3. 검증·전파"])
    await page.getByRole("button", { name, exact: true }).click();
  await expect(
    page.getByText("✓ 거래가 승인되었습니다.", { exact: false }),
  ).toBeVisible();
  await navigate(page, "채굴");
}

test("한 번 계산하기는 백엔드에서 Nonce 하나씩 실제로 계산한다", async ({
  page,
}) => {
  await page.goto("/");
  await startAndSubmit(page, "4");
  const step = page.getByRole("button", { name: "한 번 계산하기", exact: true });
  await step.click();
  await expect(page.getByTestId("attempts")).toHaveText("1");
  await expect(page.getByTestId("nonce")).toHaveText("0");
  // 난이도 4의 첫 계산이 우연히 성공하면 그 실제 성공을 그대로 인정한다.
  if (!(await page.getByText("● 일시정지", { exact: true }).isVisible())) {
    await expect(
      page.getByText("✓ 채굴 성공 ·", { exact: false }),
    ).toBeVisible();
    return;
  }
  const first = await page.getByTestId("mining-hash").textContent();
  expect(first).toMatch(/^[0-9a-f]{64}$/);
  await step.click();
  await expect(page.getByTestId("attempts")).toHaveText("2");
  await expect(page.getByTestId("nonce")).toHaveText("1");
  await expect(page.getByTestId("mining-hash")).not.toHaveText(first!);
  await expect(page.getByTestId("mining-sample")).toHaveCount(2);
  // 한 번 계산은 관찰 대기 시간을 쌓지 않고, 스스로 계산을 이어 가지 않는다.
  await expect(page.getByTestId("observation-time")).toHaveText("0.000s");
  await page.waitForTimeout(400);
  await expect(page.getByTestId("attempts")).toHaveText("2");
  await page.getByRole("button", { name: "채굴 중지", exact: true }).click();
  await expect(page.getByText("● 중지됨", { exact: true })).toBeVisible();
});

test("초기화 이후 도착하는 이전 채굴 작업의 결과를 무시한다", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  // 첫 실습의 계산 요청 하나를 붙잡아 두었다가, 새 실습이 시작된 뒤에 "성공"으로 응답한다.
  let release: (() => void) | undefined;
  let held = false;
  await page.route("**/api/labs/*/mining/*/tick", async (route) => {
    if (held) return route.continue();
    held = true;
    const [, , , , , jobId] = new URL(route.request().url()).pathname.split(
      "/",
    );
    await new Promise<void>((resolve) => {
      release = resolve;
    });
    await route.fulfill({
      json: {
        mining: {
          jobId,
          seq: 999,
          status: "found",
          node: "A",
          mode: "learning",
          attempts: 777,
          elapsedMs: 1,
          observationMs: 1,
          samples: [],
          block: {
            sessionId: "1".repeat(64),
            height: 1,
            previousHash: "0".repeat(64),
            transactionsHash: "2".repeat(64),
            timestamp: 1000,
            difficulty: 4,
            nonce: 776,
            transactions: [],
            hash: "0".repeat(64),
          },
          message: null,
          nextTickInMs: null,
        },
        lab: null,
      },
    });
  });
  await page.goto("/");
  await startAndSubmit(page, "4");
  await page
    .getByRole("button", { name: "천천히 학습 실행", exact: true })
    .click();
  await expect.poll(() => held).toBe(true);
  await page.getByRole("button", { name: "초기화 ↺", exact: true }).click();
  await page.getByRole("button", { name: "초기화 확인", exact: true }).click();
  // 페이지를 다시 불러오지 않고 같은 화면에서 새 실습을 시작한다.
  await startAndSubmit(page, "4");
  release!();
  await page.waitForTimeout(350);
  await expect(page.getByTestId("attempts")).toHaveText("0");
  await expect(page.getByText("● 준비", { exact: true })).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
  expect(errors).toEqual([]);
});
