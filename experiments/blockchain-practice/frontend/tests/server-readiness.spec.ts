import { expect, test, type Page } from "./team-test";

const configuration = "**/api/education/classroom/config";
const draftRoute = "**/api/education/labs/*/nodes/A/drafts";

async function startExample(page: Page) {
  await page.goto("/advanced");
  await page.getByLabel("실습 난이도", { exact: true }).selectOption("1");
  await page.getByRole("button", { name: "예제 실습 시작" }).click();
  await expect(page.getByTestId("balance-0")).toHaveText("100코인");
}

test("휴대폰에서 서버 준비 응답 뒤 같은 참여 링크를 자동으로 연다", async ({ page }, info) => {
  await page.setViewportSize({ width: 390, height: 844 });
  let configurationRequests = 0;
  let prepared = false;
  let mutations = 0;
  let navigations = 0;
  page.on("request", (request) => {
    if (request.isNavigationRequest() && request.frame() === page.mainFrame()) navigations += 1;
    if (request.url().includes("/api/education/") && request.method() !== "GET") mutations += 1;
  });
  await page.route(configuration, async (route) => {
    configurationRequests += 1;
    if (prepared) return route.continue();
    if (configurationRequests === 1) {
      return route.fulfill({ status: 503, json: { error: { code: "READ_FAILED", message: "저장소 준비 중" } } });
    }
    return route.fulfill({ status: 200, contentType: "text/html", body: "<!doctype html><title>Preparing service</title>" });
  });
  await page.goto("/?classroom=join&room=V5JHT522");
  await expect(page.getByTestId("server-connection")).toContainText("서버 준비 중");
  await expect.poll(() => configurationRequests, { timeout: 20_000 }).toBeGreaterThanOrEqual(2);
  await expect(page.getByLabel("별명", { exact: true })).toHaveCount(0);
  await expect(page.getByText(/백엔드.*실행|Flask 서버 실행/)).toHaveCount(0);
  await page.screenshot({ path: info.outputPath("phone-server-preparing.png"), fullPage: true });
  prepared = true;
  await expect(page.getByLabel("별명", { exact: true })).toBeVisible({ timeout: 20_000 });
  await expect(page.getByLabel("방 코드", { exact: true })).toHaveValue("V5JHT522");
  await expect(page.getByTestId("server-connection")).toHaveCount(0);
  await expect(page).toHaveURL(/\?classroom=join&room=V5JHT522$/);
  expect(navigations).toBe(1);
  expect(mutations).toBe(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  await page.screenshot({ path: info.outputPath("phone-server-ready.png"), fullPage: true });
});

test("응답을 잃은 거래 요청은 준비 안내 뒤 자동 재실행하지 않고 입력을 유지한다", async ({ page }) => {
  let resumeConfiguration: (() => void) | undefined;
  let recovering = false;
  let draftRequests = 0;
  let executedOnServer = false;
  await page.route(configuration, async (route) => {
    if (recovering) await new Promise<void>((resolve) => { resumeConfiguration = resolve; });
    await route.continue();
  });
  await startExample(page);
  await page.getByLabel("송금액").fill("23");
  await page.route(draftRoute, async (route) => {
    draftRequests += 1;
    if (draftRequests !== 1) return route.continue();
    // The real server accepts the request; only its acknowledgement is lost.
    const accepted = await route.fetch();
    expect(accepted.ok()).toBe(true);
    executedOnServer = true;
    recovering = true;
    await route.fulfill({ status: 502, contentType: "text/html", body: "<title>Service starting</title>" });
  });
  await page.getByRole("button", { name: "1. 거래 작성", exact: true }).click();
  await expect(page.getByTestId("server-connection")).toContainText("서버 준비 중");
  await expect(page.getByLabel("송금액")).toBeDisabled();
  await expect(page.getByLabel("송금액")).toHaveValue("23");
  await expect(page.getByTestId("balance-0")).toHaveText("100코인");
  expect(executedOnServer).toBe(true);
  await expect.poll(() => !!resumeConfiguration).toBe(true);
  recovering = false;
  resumeConfiguration?.();
  await expect(page.getByTestId("server-connection")).toHaveCount(0);
  await expect(page.getByLabel("송금액")).toBeEnabled();
  await expect(page.getByLabel("송금액")).toHaveValue("23");
  await expect(page.getByText(/서버 준비 중/)).toHaveCount(0);
  await expect(page.getByRole("alert").filter({ hasText: "작업이 처리됐을 수" })).toBeVisible();
  await expect(page.getByRole("button", { name: "2. 전자서명", exact: true })).toBeDisabled();
  expect(draftRequests).toBe(1);
  await page.getByRole("button", { name: "1. 거래 작성", exact: true }).click();
  await expect(page.getByRole("button", { name: "2. 전자서명", exact: true })).toBeEnabled();
  expect(draftRequests).toBe(2);
  await expect(page.getByTestId("balance-0")).toHaveText("100코인");
});

test("실제 거래 검증 오류는 서버 준비 중으로 바꾸지 않는다", async ({ page }) => {
  let configurationRequests = 0;
  await page.route(configuration, async (route) => {
    configurationRequests += 1;
    await route.continue();
  });
  await startExample(page);
  await page.getByLabel("송금액").fill("1000");
  const rejected = page.waitForResponse((response) => response.url().endsWith("/nodes/A/drafts"));
  await page.getByRole("button", { name: "1. 거래 작성", exact: true }).click();
  const response = await rejected;
  expect(response.status()).toBeGreaterThanOrEqual(400);
  expect(response.status()).toBeLessThan(500);
  const error = (await response.json()).error;
  await expect(page.getByRole("alert").filter({ hasText: error.message.replace(/([가-힣])\.(?=\s|$)/g, "$1") })).toBeVisible();
  await expect(page.getByTestId("server-connection")).toHaveCount(0);
  await expect(page.getByLabel("송금액")).toBeEnabled();
  expect(configurationRequests).toBe(1);
});

test("준비 상태가 아닌 접근 오류는 안내하고 직접 다시 연결할 수 있다", async ({ page }) => {
  let configurationRequests = 0;
  let allowed = false;
  await page.route(configuration, async (route) => {
    configurationRequests += 1;
    if (allowed) return route.continue();
    return route.fulfill({ status: 403, json: { error: { code: "FORBIDDEN", message: "접속 권한을 확인하세요" } } });
  });
  await page.goto("/?classroom=join&room=V5JHT522");
  await expect(page.getByTestId("server-connection")).toContainText("실습 서버 연결 확인");
  await expect(page.getByLabel("별명", { exact: true })).toHaveCount(0);
  expect(configurationRequests).toBe(1);
  allowed = true;
  await page.getByRole("button", { name: "다시 연결", exact: true }).click();
  await expect(page.getByLabel("별명", { exact: true })).toBeVisible();
  await expect(page.getByLabel("방 코드", { exact: true })).toHaveValue("V5JHT522");
  expect(configurationRequests).toBe(2);
});

test("기초 실습도 서버 준비 뒤 새로고침 없이 저장 기록을 읽는다", async ({ page }) => {
  let prepared = false;
  let reads = 0;
  let mutations = 0;
  page.on("request", (request) => {
    if (request.url().includes("/api/") && request.method() !== "GET") mutations += 1;
  });
  await page.route("**/api/state", async (route) => {
    reads += 1;
    if (prepared) return route.continue();
    return route.fulfill({ status: 502, contentType: "text/html", body: "<title>Preparing service</title>" });
  });
  await page.goto("/basic");
  await expect(page.locator("#global-message")).toContainText("서버 준비 중");
  await expect(page.getByTestId("practice-reset")).toBeDisabled();
  await expect.poll(() => reads, { timeout: 20_000 }).toBeGreaterThanOrEqual(2);
  prepared = true;
  await expect(page.getByTestId("practice-reset")).toBeEnabled({ timeout: 20_000 });
  await expect(page.locator("#practice-label")).not.toHaveText("실습 불러오는 중");
  await expect(page.locator("#global-message")).not.toContainText("서버 준비 중");
  expect(mutations).toBe(0);
});

test("기초 지갑 생성의 응답을 잃어도 조회만 복구하고 이름을 유지한다", async ({ page }) => {
  let recovering = false;
  let creates = 0;
  const heldReads: Array<() => void> = [];
  await page.route("**/api/state", async (route) => {
    if (recovering) await new Promise<void>((resolve) => { heldReads.push(resolve); });
    await route.continue();
  });
  await page.goto("/basic");
  await expect(page.getByTestId("practice-reset")).toBeEnabled();
  await page.locator('nav [data-page="wallet"]').click();
  const name = "연결 복구 확인";
  await page.getByTestId("wallet-name").fill(name);
  await page.route("**/api/wallets", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    creates += 1;
    const accepted = await route.fetch();
    expect(accepted.ok()).toBe(true);
    recovering = true;
    await route.fulfill({ status: 502, contentType: "text/html", body: "<title>Service starting</title>" });
  });
  await page.getByTestId("wallet-create").click();
  await expect(page.locator("#global-message")).toContainText("서버 준비 중");
  await expect(page.getByTestId("wallet-name")).toBeDisabled();
  await expect(page.getByTestId("wallet-name")).toHaveValue(name);
  await expect.poll(() => heldReads.length).toBeGreaterThan(0);
  recovering = false;
  heldReads.forEach((resolve) => resolve());
  await expect(page.getByTestId("wallet-create")).toBeEnabled();
  await expect(page.getByTestId("wallet-name")).toHaveValue(name);
  await expect(page.locator("#global-message")).not.toContainText("서버 준비 중");
  const wallets = await (await page.request.get("/api/wallets?node=A")).json();
  expect(wallets.filter((wallet: { name: string }) => wallet.name === name)).toHaveLength(1);
  expect(creates).toBe(1);
});
