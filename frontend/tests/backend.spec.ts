import { expect, test, type Page } from "@playwright/test";

// 화면과 Python 백엔드를 나눈 뒤 생긴 상황: 연결 실패, 사라진 실습, 개인키 조회.
const startButton = (page: Page) =>
  page.getByRole("button", { name: /^예제 실습 시작/ });
// 개발 서버가 제공하는 화면 소스(/src/api/...)와 구분해 실제 API 요청만 고른다.
const isApi = (url: URL) => url.pathname.startsWith("/api/");

test("백엔드에 연결할 수 없으면 이유를 안내하고 복구 후 다시 시작할 수 있다", async ({
  page,
}) => {
  // 백엔드가 꺼져 있을 때 개발 서버의 프록시가 돌려주는 것과 같은, JSON이 아닌 오류 응답.
  await page.route(isApi, (route) =>
    route.fulfill({
      status: 502,
      contentType: "text/plain",
      body: "Bad Gateway",
    }),
  );
  await page.goto("/");
  await startButton(page).click();
  await expect(page.getByRole("alert")).toContainText(
    "백엔드에 연결할 수 없습니다",
  );
  await expect(startButton(page)).toBeVisible();
  await expect(page.getByTestId("balance-0")).toHaveCount(0);
  await page.unroute(isApi);
  await startButton(page).click();
  await expect(page.getByTestId("balance-0")).toHaveText("100코인");
  await expect(page.getByRole("alert")).toHaveCount(0);
});

test("백엔드에 실습이 남아 있지 않으면 안내하고 초기화 후 새 실습을 시작한다", async ({
  page,
}) => {
  await page.goto("/");
  const created = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/labs") &&
      response.request().method() === "POST",
  );
  await startButton(page).click();
  const { lab } = (await (await created).json()) as { lab: { labId: string } };
  await expect(page.getByTestId("balance-0")).toHaveText("100코인");
  // 백엔드를 다시 시작했을 때처럼, 서버가 이 실습을 더 이상 갖고 있지 않게 만든다.
  expect((await page.request.delete(`/api/labs/${lab.labId}`)).status()).toBe(
    204,
  );
  await page.getByRole("button", { name: "1. 거래 작성", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText(
    "실습을 찾을 수 없습니다",
  );
  await page.getByRole("button", { name: "초기화 ↺", exact: true }).click();
  await page.getByRole("button", { name: "초기화 확인", exact: true }).click();
  await startButton(page).click();
  await expect(page.getByTestId("balance-0")).toHaveText("100코인");
  await page.getByRole("button", { name: "1. 거래 작성", exact: true }).click();
  await expect(page.getByTestId("flow-1")).toHaveAttribute(
    "data-state",
    "current",
  );
});

test("개인키는 펼쳐 볼 때만 백엔드에서 가져오며 실습 상태 응답에는 없다", async ({
  page,
}) => {
  const bodies: Promise<string>[] = [];
  page.on("response", (response) => {
    const url = new URL(response.url());
    if (isApi(url) && !url.pathname.endsWith("/private-key"))
      bodies.push(response.text().catch(() => ""));
  });
  await page.goto("/");
  await startButton(page).click();
  for (const name of ["1. 거래 작성", "2. 전자서명", "3. 검증·전파"])
    await page.getByRole("button", { name, exact: true }).click();
  await expect(
    page.getByText("✓ 거래가 승인되었습니다.", { exact: false }),
  ).toBeVisible();

  const card = page.locator(".wallet-card").first();
  await card.getByText("공개키·개인키·UTXO 보기", { exact: true }).click();
  const revealed = page.waitForResponse((response) =>
    response.url().endsWith("/private-key"),
  );
  await card.getByText("교육용 개인키 펼쳐 보기", { exact: true }).click();
  expect((await revealed).headers()["cache-control"]).toBe("no-store");
  const key = card.locator(".private-key .hash");
  await expect(key).toHaveText(/^[0-9a-f]{64}$/);
  const privateKey = (await key.textContent())!;

  const responses = await Promise.all(bodies);
  expect(responses.length).toBeGreaterThanOrEqual(4);
  for (const body of responses) expect(body).not.toContain(privateKey);
  expect(responses.join("")).not.toContain("privateKey");
});
