import { expect, test, type Page } from "@playwright/test";

const nav = (page: Page, name: string) =>
  page
    .getByRole("navigation")
    .getByRole("button", { name: new RegExp(name) })
    .click();
async function start(page: Page, difficulty = "1") {
  await page.goto("/");
  await page
    .getByLabel("실습 난이도", { exact: true })
    .selectOption(difficulty);
  await page.getByRole("button", { name: "예제 실습 시작" }).click();
  await expect(page.getByTestId("balance-0")).toBeVisible();
}
async function send(page: Page) {
  await page.getByRole("button", { name: "1. 거래 작성", exact: true }).click();
  await page.getByRole("button", { name: "2. 전자서명", exact: true }).click();
  await page.getByRole("button", { name: "3. 검증·전파", exact: true }).click();
  await expect(page.getByTestId("balance-comparison")).toContainText("100 / 0");
}

test("학습 안내는 실행 없이 조작 위치로 이동하고 실제 잔액·노드·요약을 연결한다", async ({
  page,
}, info) => {
  await start(page);
  const guide = page.getByRole("region", { name: "학습 안내" });
  await guide.getByRole("button").click();
  await expect(
    page.getByRole("button", { name: "1. 거래 작성", exact: true }),
  ).toBeFocused();
  await expect(page.getByTestId("utxo-totals")).toHaveCount(0);
  await send(page);
  await page.screenshot({
    path: info.outputPath("2.2-wallet.png"),
    fullPage: true,
  });
  await guide.getByRole("button", { name: "대기 상태 살펴보기" }).click();
  await expect(page.getByTestId("presence-A")).toContainText("Mempool 대기");
  await expect(page.getByTestId("presence-B")).toContainText("미수신");
  await guide.getByRole("button", { name: "채굴 준비 살펴보기" }).click();
  await page
    .getByRole("button", { name: "연속 채굴 시작", exact: true })
    .click();
  await expect(page.getByText("✓ 채굴 성공 ·", { exact: false })).toBeVisible();
  await nav(page, "노드·블록체인");
  await expect(page.getByTestId("presence-A")).toContainText("블록 #1 포함");
  await expect(page.getByTestId("presence-B")).toContainText("미수신");
  await page.getByRole("checkbox", { name: "메시지 자동 전달" }).check();
  await expect(
    page.getByRole("heading", { name: "이번 거래로 확인한 것" }),
  ).toBeVisible();
  await page.screenshot({
    path: info.outputPath("2.2-network.png"),
    fullPage: true,
  });
  await page
    .getByRole("button", { name: "변조 실험으로 이동", exact: true })
    .click();
  await page
    .getByRole("button", { name: "복사본 변조·검증", exact: true })
    .click();
  await expect(page.getByTestId("tamper-change")).toContainText("30 → 999");
  await expect(page.getByTestId("tamper-change")).toContainText(
    "서명했던 금액과 달라졌습니다",
  );
  await page.screenshot({
    path: info.outputPath("2.2-tamper.png"),
    fullPage: true,
  });
  await nav(page, "지갑·거래");
  await page
    .getByRole("button", { name: "새 거래 만들기", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "이번 거래로 확인한 것" }),
  ).toHaveCount(0);
  await expect(page.getByTestId("balance-comparison")).toHaveCount(0);
});

test("늦은 작성 응답과 중복 클릭이 새 입력을 덮어쓰지 않는다", async ({
  page,
}) => {
  await start(page);
  let requests = 0;
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/api/labs/*/nodes/*/drafts", async (route) => {
    requests++;
    const response = await route.fetch();
    await gate;
    await route.fulfill({ response });
  });
  const draft = page.getByRole("button", { name: "1. 거래 작성", exact: true });
  await draft.click();
  await expect(draft).toBeDisabled();
  await expect(
    page.getByText("거래 작성 처리 중…", { exact: true }),
  ).toBeVisible();
  await page.getByLabel("송금액").fill("40");
  release();
  await expect(draft).toBeEnabled();
  expect(requests).toBe(1);
  await expect(page.getByTestId("utxo-totals")).toHaveCount(0);
  await draft.click();
  await expect(page.getByTestId("utxo-totals")).toContainText("40 코인");
});

test("선택한 실제 표본은 다음 계산 뒤에도 유지되고 새 채굴에서는 초기화된다", async ({
  page,
}, info) => {
  await start(page, "4");
  await send(page);
  await nav(page, "채굴");
  await page
    .getByRole("button", { name: "한 번 계산하기", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Nonce 0 표본 비교", exact: true })
    .click();
  await expect(page.getByTestId("sample-inspector")).toContainText(
    "선택한 표본 · Nonce 0",
  );
  if (
    await page
      .getByRole("button", { name: "채굴 중지", exact: true })
      .isVisible()
  ) {
    await page
      .getByRole("button", { name: "한 번 계산하기", exact: true })
      .click();
    await expect(page.getByTestId("sample-inspector")).toContainText(
      "현재 최신 계산: Nonce 1",
    );
    await expect(page.getByTestId("sample-inspector")).toContainText(
      "선택한 표본 · Nonce 0",
    );
    if (
      await page
        .getByRole("button", { name: "채굴 중지", exact: true })
        .isVisible()
    ) {
      await page
        .getByRole("button", { name: "채굴 중지", exact: true })
        .click();
      await page
        .getByRole("button", { name: "한 번 계산하기", exact: true })
        .click();
      await expect(page.getByTestId("sample-inspector")).toHaveCount(0);
    }
  }
  await page.getByLabel("화면 테마", { exact: true }).selectOption("dark");
  await page.screenshot({
    path: info.outputPath("2.2-mining-dark.png"),
    fullPage: true,
  });
});

test("좁은 화면과 모션 감소에서도 안내와 결과를 읽을 수 있다", async ({
  page,
}, info) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await start(page);
  await send(page);
  for (const theme of ["light", "dark"]) {
    await page.getByLabel("화면 테마", { exact: true }).selectOption(theme);
    await expect(page.getByRole("region", { name: "학습 안내" })).toBeVisible();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    await expect(page.locator(".validation")).toHaveCSS(
      "animation-name",
      "none",
    );
    await page.screenshot({
      path: info.outputPath(`2.2-mobile-${theme}.png`),
      fullPage: true,
    });
  }
});
