import { expect, test, type Page } from "@playwright/test";

async function capture(page: Page, path: string) {
  // 전체 페이지 캡처 시 고정 사이드바와 화면 밖 skip link의 위치를 일치시킨다.
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path, fullPage: true });
}

const navigate = (page: Page, title: string) =>
  page
    .getByRole("navigation")
    .getByRole("button", { name: new RegExp(title) })
    .click();
async function start(page: Page, difficulty = "1") {
  await page.goto("/");
  await page
    .getByLabel("실습 난이도", { exact: true })
    .selectOption(difficulty);
  await page.getByRole("button", { name: "예제 실습 시작" }).click();
}
async function send(page: Page, amount = "30") {
  await page.getByLabel("송금액").fill(amount);
  await page.getByRole("button", { name: "1. 거래 작성", exact: true }).click();
  await page.getByRole("button", { name: "2. 전자서명", exact: true }).click();
  await page.getByRole("button", { name: "3. 검증·전파", exact: true }).click();
  await expect(
    page.getByText("✓ 거래가 승인되었습니다.", { exact: false }),
  ).toBeVisible();
}
async function mine(page: Page) {
  await navigate(page, "채굴");
  await page
    .getByRole("button", { name: "연속 채굴 시작", exact: true })
    .click();
  await expect(page.getByText("✓ 채굴 성공 ·", { exact: false })).toBeVisible();
  await page.getByRole("button", { name: "노드 전파 확인" }).click();
  await expect(
    page.getByRole("heading", { name: "전달 대기열 · 0", exact: true }),
  ).toBeVisible();
}

test("지갑 → 서명 → 검증 → PoW → 3개 노드 → 변조의 전체 흐름", async ({
  page,
}, testInfo) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/");
  await capture(page, testInfo.outputPath("01-home.png"));
  await start(page);
  await expect(page.getByTestId("balance-0")).toHaveText("100코인");
  await send(page);
  await expect(page.getByTestId("balance-0")).toHaveText("100코인");
  await navigate(page, "노드·블록체인");
  await page.getByRole("button", { name: "잘못된 PoW 블록 수신 실험" }).click();
  await expect(page.getByText("POW", { exact: true })).toBeVisible();
  await navigate(page, "지갑·거래");
  await capture(page, testInfo.outputPath("02-wallet.png"));
  await navigate(page, "검증·Mempool");
  await expect(
    page.getByRole("heading", { name: "Node A", exact: true }),
  ).toBeVisible();
  await mine(page);
  for (const id of ["A", "B", "C"]) {
    await page.getByLabel("관찰 노드", { exact: true }).selectOption(id);
    await navigate(page, "지갑·거래");
    await expect(page.getByTestId("balance-0")).toHaveText("70코인");
    await expect(page.getByTestId("balance-1")).toHaveText("30코인");
    await navigate(page, "노드·블록체인");
  }
  await capture(page, testInfo.outputPath("03-network.png"));
  // 두 번째 블록을 만들고 과거 블록과 후속 블록의 연결을 검사한다.
  await navigate(page, "지갑·거래");
  await send(page, "10");
  await mine(page);
  await navigate(page, "변조 실험");
  await page
    .getByRole("button", { name: "복사본 변조·검증", exact: true })
    .click();
  await expect(
    page.getByText("TRANSACTIONS_HASH", { exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "해시만 재계산", exact: true })
    .click();
  await expect(
    page
      .locator(".diagnostics li")
      .filter({ hasText: "다음 블록의 Previous Hash 연결" }),
  ).toContainText("✕ 실패");
  await capture(page, testInfo.outputPath("04-tamper.png"));
  await navigate(page, "지갑·거래");
  await expect(page.getByTestId("balance-0")).toHaveText("60코인");
  await expect(page.getByTestId("balance-1")).toHaveText("40코인");
  expect(errors).toEqual([]);
});

test("잔액 부족·잘못된 서명·이중 지출의 이유를 보여준다", async ({ page }) => {
  await start(page);
  await page.getByLabel("송금액").fill("101");
  await page.getByRole("button", { name: "1. 거래 작성", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("잔액 부족");
  await send(page);
  await page.getByRole("button", { name: "잘못된 서명 실험" }).click();
  await expect(page.getByText("SIGNATURE", { exact: true })).toBeVisible();
  // 다른 노드의 두 번의 자동 수신 후에도 선택 노드의 실패 설명을 유지해야 한다.
  await page.waitForTimeout(1600);
  await expect(page.getByText("SIGNATURE", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "이중 지출 실험" }).click();
  await expect(page.getByText("DOUBLE_SPEND", { exact: true })).toBeVisible();
  await expect(page.getByTestId("balance-0")).toHaveText("100코인");
});

test("수동 전달은 한 노드씩 반영되고 마지막 블록 전달 전 채굴을 막는다", async ({
  page,
}) => {
  await start(page);
  await navigate(page, "노드·블록체인");
  await page.getByRole("checkbox", { name: "메시지 자동 전달" }).uncheck();
  await navigate(page, "지갑·거래");
  await send(page);
  await navigate(page, "노드·블록체인");
  await expect(
    page.getByRole("heading", { name: "전달 대기열 · 2" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "메시지 1개 전달" }).click();
  await expect(
    page.getByRole("heading", { name: "전달 대기열 · 1" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "메시지 1개 전달" }).click();
  await navigate(page, "채굴");
  await page
    .getByRole("button", { name: "연속 채굴 시작", exact: true })
    .click();
  await expect(page.getByText("✓ 채굴 성공 ·", { exact: false })).toBeVisible();
  await page.getByRole("button", { name: "노드 전파 확인" }).click();
  await expect(
    page.getByRole("heading", { name: "전달 대기열 · 2" }),
  ).toBeVisible();
  await page.getByLabel("관찰 노드", { exact: true }).selectOption("B");
  await navigate(page, "채굴");
  await expect(
    page.getByRole("button", { name: "연속 채굴 시작", exact: true }),
  ).toBeDisabled();
  await navigate(page, "노드·블록체인");
  await page.getByRole("button", { name: "메시지 1개 전달" }).click();
  await page.getByRole("button", { name: "메시지 1개 전달" }).click();
  await navigate(page, "지갑·거래");
  await expect(page.getByTestId("balance-0")).toHaveText("70코인");
});

test("직접 지갑 생성·새로고침·새 실습 난이도 선택", async ({ page }) => {
  await page.goto("/");
  await page
    .getByRole("button", { name: "직접 시작: Alice 지갑 생성", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Bob 지갑 생성", exact: true })
    .click();
  await page.getByRole("button", { name: "생성한 지갑으로 시작" }).click();
  await page.getByRole("button", { name: "＋ 지갑 추가", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "학생 3", exact: true }),
  ).toBeVisible();
  await expect(page.getByTestId("balance-2")).toHaveText("0코인");
  await page.getByRole("button", { name: "초기화 ↺", exact: true }).click();
  await page.getByRole("button", { name: "취소", exact: true }).click();
  await expect(page.getByTestId("balance-0")).toHaveText("100코인");
  await page.getByRole("button", { name: "초기화 ↺", exact: true }).click();
  await page.getByRole("button", { name: "초기화 확인", exact: true }).click();
  await page.getByLabel("실습 난이도", { exact: true }).selectOption("4");
  await page.getByRole("button", { name: "예제 실습 시작" }).click();
  await navigate(page, "채굴");
  await expect(page.getByText("1 / 65,536", { exact: true })).toBeVisible();
  await page.reload();
  await expect(
    page.getByRole("button", { name: "예제 실습 시작" }),
  ).toBeVisible();
});

test("설치 후 실습은 외부 네트워크 없이 실행되며 화면이 가로로 넘치지 않는다", async ({
  page,
}, testInfo) => {
  const external: string[] = [];
  await page.route("**/*", (route) => {
    const url = route.request().url();
    if (url.startsWith("http") && !url.startsWith("http://127.0.0.1:4173/")) {
      external.push(url);
      return route.abort();
    }
    return route.continue();
  });
  await start(page);
  await send(page);
  await mine(page);
  for (const width of [1440, 1024, 390]) {
    await page.setViewportSize({ width, height: 900 });
    for (const title of ["지갑·거래", "채굴", "노드·블록체인", "변조 실험"]) {
      await navigate(page, title);
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      ).toBe(true);
    }
    await capture(page, testInfo.outputPath(`responsive-${width}.png`));
  }
  expect(external).toEqual([]);
});

test("키보드로 본문과 초기화 확인을 조작한다", async ({ page }) => {
  await page.goto("/");
  await page.keyboard.press("Tab");
  await expect(
    page.getByRole("link", { name: "본문으로 바로가기" }),
  ).toBeFocused();
  await page.keyboard.press("Enter");
  await start(page);
  await page.getByRole("button", { name: "초기화 ↺", exact: true }).focus();
  await page.keyboard.press("Enter");
  await expect(
    page.getByRole("button", { name: "초기화 확인", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "취소", exact: true }).focus();
  await page.keyboard.press("Enter");
  await expect(
    page.getByRole("button", { name: "초기화 확인", exact: true }),
  ).toBeHidden();
});
