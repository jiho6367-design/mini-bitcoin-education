import { expect, test, type Page } from "./team-test";
import type { LabSnapshot } from "../src/api/types";

const nav = (page: Page, name: string) =>
  page
    .getByRole("navigation", { name: "실습 화면" })
    .getByRole("button", { name: new RegExp(name) })
    .click();

async function submit(page: Page) {
  for (const name of ["1. 거래 작성", "2. 전자서명", "3. 검증·전파"])
    await page.getByRole("button", { name, exact: true }).click();
  await expect(
    page.getByText("✓ 거래가 승인되었습니다", { exact: false }),
  ).toBeVisible();
}

test("UTXO 직접 선택 → 서명 → 세 채굴자 경쟁 → 보상 성숙 → 보상 송금", async ({
  page,
}, info) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  let latest: LabSnapshot | undefined;
  page.on("response", async (response) => {
    if (new URL(response.url()).pathname.startsWith("/api/education/labs")) {
      const body = await response.json().catch(() => null);
      if (body?.lab?.phase === "running") latest = body.lab;
    }
  });
  await page.goto("/advanced");
  await page.getByLabel("실습 난이도", { exact: true }).selectOption("1");
  await page.getByRole("button", { name: /예제 실습 시작/ }).click();
  await page
    .getByRole("checkbox", { name: "UTXO 직접 선택", exact: true })
    .check();
  await page.locator(".utxo-choice input").check();
  await submit(page);
  await expect(page.getByTestId("utxo-totals")).toContainText("69 코인");
  await expect(page.getByLabel("서명 과정")).toContainText("서명 생성 완료");
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: info.outputPath("3.0-signature-utxo.png"),
    fullPage: true,
  });
  await nav(page, "노드·블록체인");
  await page.getByRole("checkbox", { name: "메시지 자동 전달" }).check();
  await expect(
    page.getByRole("heading", { name: "전달 대기열 · 0", exact: true }),
  ).toBeVisible();
  await nav(page, "채굴");
  await page.getByLabel("계산 속도").selectOption("fast");
  await page.getByRole("button", { name: "세 후보 준비", exact: true }).click();
  await expect(page.locator(".race-card")).toHaveCount(3);
  await expect(page.locator(".race-card").first()).toContainText(
    "첫 계산을 기다립니다",
  );
  await page
    .getByRole("button", { name: "경쟁 시작 / 계속", exact: true })
    .click();
  await expect(page.getByText(/설명을 위한 전체 일시정지입니다/)).toBeVisible();
  const winner = latest!.race!.completed.at(-1)!.node;
  expect(latest!.nodes[winner].chain[1].transactions[0].outputs[0].amount).toBe(
    51,
  );
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: info.outputPath("3.0-race.png"),
    fullPage: true,
  });
  await nav(page, "노드·블록체인");
  await page.getByRole("checkbox", { name: "메시지 자동 전달" }).check();
  await expect(
    page.getByRole("heading", { name: "전달 대기열 · 0", exact: true }),
  ).toBeVisible();
  await nav(page, "지갑·거래");
  await expect(page.getByTestId("balance-0")).toHaveText("69코인");
  await expect(page.getByTestId("balance-1")).toHaveText("30코인");
  await nav(page, "채굴");
  await page
    .getByRole("button", { name: "이 보상 성숙까지 빠른 진행", exact: true })
    .click();
  await expect(page.getByText(/성숙 완료 · 선택 보상을/)).toBeVisible({
    timeout: 60000,
  });
  await expect(
    page
      .locator(".race-card")
      .filter({
        has: page.getByRole("heading", {
          name: `채굴자 ${winner}`,
          exact: true,
        }),
      }),
  ).toContainText("다음 블록에 사용 가능 51");
  await nav(page, "지갑·거래");
  await page
    .getByRole("combobox", { name: "보내는 지갑", exact: true })
    .selectOption({ label: `채굴자 ${winner}` });
  await page.getByLabel("송금액").fill("1");
  await submit(page);
  await expect(page.getByLabel("거래 여정")).toContainText("Mempool 대기");
  expect(errors).toEqual([]);
});

test("경쟁 화면은 작은 화면·다크·동작 줄이기에서도 상태와 제어를 보여준다", async ({
  page,
}, info) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/advanced");
  await page.getByRole("button", { name: /예제 실습 시작/ }).click();
  await page.getByLabel("화면 테마", { exact: true }).selectOption("dark");
  await page
    .getByRole("combobox", { name: "실습 방식", exact: true })
    .selectOption("free");
  await nav(page, "채굴");
  await page.getByRole("button", { name: "세 후보 준비", exact: true }).click();
  await page
    .getByRole("button", { name: "채굴자 B 멈춤", exact: true })
    .click();
  await page
    .getByRole("button", { name: "각자 한 번 계산", exact: true })
    .click();
  await expect(page.locator(".race-card").nth(1)).toContainText(
    "개별 일시정지",
  );
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth + 1,
    ),
  ).toBe(true);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: info.outputPath("3.0-race-mobile-dark.png"),
    fullPage: true,
  });
});
