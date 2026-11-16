import { createHash } from "node:crypto";
import { expect, test, type Page } from "./team-test";

const defaultMessage = "You are the best of best!!!!";
const defaultProof = "0002a0cc7ab05331087b7525cea18a492af097a20f4c350f5b83bd7ce05f8010";
const hash = (message: string, nonce: string) => createHash("sha256").update(message + nonce, "utf8").digest("hex");
const canonicalNonce = async (page: Page) => (await page.getByTestId("nonce-value").innerText()).replaceAll(",", "").trim();

async function ready(page: Page) {
  await expect(page.getByRole("heading", { name: "넌스 찾기", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "탐색 시작", exact: true })).toBeEnabled();
  // These regressions check the sequential scanner's exact first nonce/count.
  await page.getByRole("combobox", { name: "연산 방식", exact: true }).selectOption("cpu");
}

async function settings(page: Page) {
  const details = page.locator("details").filter({ has: page.getByText("추가 설정", { exact: true }) });
  if (await details.getAttribute("open") === null) await details.locator("summary").click();
}

async function found(page: Page, nonce: string, proof: string) {
  await expect(page.getByTestId("nonce-status")).toHaveText("조건에 맞는 넌스를 찾았습니다", { timeout: 30_000 });
  expect(await canonicalNonce(page)).toBe(nonce);
  await expect(page.getByTestId("nonce-hash")).toHaveText(proof);
  await expect(page.getByRole("button", { name: "탐색 시작", exact: true })).toBeEnabled();
}

test("원본 브랜치와 같은 실제 SHA-256 넌스를 찾고 재검증·재접속 기록을 유지한다", async ({ page }, info) => {
  const errors: string[] = [];
  const mutations: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  page.on("request", request => {
    if (request.url().includes("/api/") && !["GET", "HEAD"].includes(request.method())) mutations.push(request.url());
  });
  await page.goto("/hashcash");
  await ready(page);
  await settings(page);
  await page.getByLabel("기록 이름", { exact: true }).fill("원본 비교 참가자");
  await expect(page.getByRole("textbox", { name: "메시지", exact: true })).toHaveValue(defaultMessage);
  await page.getByRole("button", { name: "탐색 시작", exact: true }).click();
  await found(page, "3055", defaultProof);
  expect((await page.getByTestId("nonce-checked").innerText()).replaceAll(",", "")).toContain("3056");
  expect(hash(defaultMessage, "3055")).toBe(defaultProof);
  await page.getByRole("button", { name: "해시 다시 검증", exact: true }).click();
  await expect(page.getByTestId("nonce-verification")).toContainText(/일치|통과/);
  const records = page.getByTestId("nonce-history-record");
  await expect(records).toHaveCount(1);
  await expect(records.first()).toContainText("원본 비교 참가자");
  await expect(records.first()).toContainText(defaultProof);
  await page.screenshot({ path: info.outputPath("nonce-original-proof.png"), fullPage: true });

  await page.reload();
  await ready(page);
  await expect(records).toHaveCount(1);
  await expect(records.first()).toContainText(defaultProof);
  await records.first().getByRole("button", { name: "결과 조건 불러오기", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "메시지", exact: true })).toHaveValue(defaultMessage);
  await expect(page.getByLabel("목표 난이도", { exact: true })).toHaveValue("3");
  expect(mutations, "독립 넌스 탐색은 기존 실습 장부를 변경하지 않는다").toEqual([]);
  expect(errors).toEqual([]);
});

test("한글·이모지를 그대로 계산하고 uint64 끝에서 넌스를 순환시키지 않는다", async ({ page }) => {
  await page.goto("/hashcash");
  await ready(page);
  await page.getByRole("textbox", { name: "메시지", exact: true }).fill("한글🙂");
  await page.getByRole("button", { name: "탐색 시작", exact: true }).click();
  await found(page, "980", "00091f64964396f15d07238d0d352913789e1dea831702392b3ceb73d6ac99ad");
  await settings(page);
  await page.getByRole("textbox", { name: "메시지", exact: true }).fill(" 공백\n한글🙂 ");
  await page.getByLabel("목표 난이도", { exact: true }).fill("64");
  await page.getByLabel("시작 넌스", { exact: true }).fill("18446744073709551615");
  await page.getByRole("button", { name: "탐색 시작", exact: true }).click();
  await expect(page.getByTestId("nonce-status")).toContainText(/범위|끝/);
  expect(await canonicalNonce(page)).toBe("18446744073709551615");
  await expect(page.getByTestId("nonce-hash")).toHaveText(hash(" 공백\n한글🙂 ", "18446744073709551615"));
  expect((await page.getByTestId("nonce-checked").innerText()).replaceAll(",", "")).toContain("1");
  await expect(page.getByTestId("nonce-history-record")).toHaveCount(1);
});

test("실제 탐색을 중지한 뒤 새 조건으로 재시작해도 이전 작업이 결과를 덮지 않는다", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto("/hashcash");
  await ready(page);
  await page.getByLabel("목표 난이도", { exact: true }).fill("64");
  await page.getByRole("button", { name: "탐색 시작", exact: true }).click();
  await expect.poll(async () => Number((await page.getByTestId("nonce-checked").innerText()).replace(/[^\d]/g, ""))).toBeGreaterThan(0);
  await page.getByRole("button", { name: "중지", exact: true }).click();
  await expect(page.getByTestId("nonce-status")).toHaveText("탐색을 중지했습니다");
  const stopped = await page.getByTestId("nonce-checked").textContent() ?? "";
  await page.waitForTimeout(300);
  await expect(page.getByTestId("nonce-checked")).toHaveText(stopped);
  await expect(page.getByTestId("nonce-history-record")).toHaveCount(0);
  await page.getByRole("textbox", { name: "메시지", exact: true }).fill("새 탐색");
  await page.getByLabel("목표 난이도", { exact: true }).fill("1");
  let nonce = 0;
  while (!hash("새 탐색", String(nonce)).startsWith("0")) nonce += 1;
  await page.getByRole("button", { name: "탐색 시작", exact: true }).click();
  await found(page, String(nonce), hash("새 탐색", String(nonce)));
  await page.waitForTimeout(300);
  await found(page, String(nonce), hash("새 탐색", String(nonce)));
  await expect(page.getByTestId("nonce-history-record")).toHaveCount(1);
  expect(errors).toEqual([]);
});

test("브라우저 기록 저장이 막혀도 여러 결과를 현재 화면에서 유지하고 이유를 안내한다", async ({ page }) => {
  await page.addInitScript(() => {
    const original = Storage.prototype.setItem;
    Storage.prototype.setItem = function (key, value) {
      if (key === "team-hashcash-history-v1" || key === "team-hashcash-name-v1") {
        throw new DOMException("Test-only storage quota", "QuotaExceededError");
      }
      original.call(this, key, value);
    };
  });
  await page.goto("/hashcash");
  await ready(page);
  await page.getByLabel("목표 난이도", { exact: true }).fill("1");
  for (const [index, message] of ["첫 번째 저장 제한", "두 번째 저장 제한"].entries()) {
    await page.getByRole("textbox", { name: "메시지", exact: true }).fill(message);
    let nonce = 0;
    while (!hash(message, String(nonce)).startsWith("0")) nonce += 1;
    await page.getByRole("button", { name: "탐색 시작", exact: true }).click();
    await found(page, String(nonce), hash(message, String(nonce)));
    await expect(page.getByTestId("nonce-history-record")).toHaveCount(index + 1);
    await expect(page.locator(".nonce-storage-message")).toContainText("현재 화면");
  }
  await expect(page.getByTestId("nonce-history")).toContainText("첫 번째 저장 제한");
  await expect(page.getByTestId("nonce-history")).toContainText("두 번째 저장 제한");
  expect(await page.evaluate(() => localStorage.getItem("team-hashcash-history-v1"))).toBeNull();
});

test("다른 탭으로 이동하고 브라우저 뒤로 돌아와도 떠난 탐색은 계속 실행되지 않는다", async ({ page }) => {
  await page.goto("/hashcash");
  await ready(page);
  await page.getByLabel("목표 난이도", { exact: true }).fill("64");
  await page.getByRole("button", { name: "탐색 시작", exact: true }).click();
  await expect.poll(async () => Number((await page.getByTestId("nonce-checked").innerText()).replace(/[^\d]/g, ""))).toBeGreaterThan(0);
  await page.getByRole("navigation", { name: "실습 모드", exact: true }).getByRole("link", { name: "기초 실습", exact: true }).click();
  await expect(page.getByTestId("node-select")).toBeEnabled();
  await page.goBack();
  await ready(page);
  await expect(page.getByTestId("nonce-status")).toHaveText(/^(입력 후 시작하세요|탐색을 중지했습니다)$/);
  const checked = await page.getByTestId("nonce-checked").textContent() ?? "";
  await page.waitForTimeout(300);
  await expect(page.getByTestId("nonce-checked")).toHaveText(checked);
  await expect(page.getByTestId("nonce-history-record")).toHaveCount(0);
});

for (const theme of ["light", "dark"] as const) {
  test(`${theme} 휴대폰에서 세 탭의 색상·선택 상태를 유지하며 이동한다`, async ({ page }, info) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/");
    await expect(page.getByTestId("node-select")).toBeEnabled();
    await page.getByLabel("화면 테마", { exact: true }).selectOption(theme);
    const tokens = await page.evaluate(() => {
      const style = getComputedStyle(document.documentElement);
      return ["--page-bg", "--surface", "--text", "--muted", "--accent", "--selected-bg"].map(key =>
        style.getPropertyValue(key).trim().replace(/^#([\da-f])([\da-f])([\da-f])$/i, "#$1$1$2$2$3$3"));
    });
    const nav = page.getByRole("navigation", { name: "실습 모드", exact: true });
    for (const tab of ["넌스 찾기", "공동 실습", "넌스 찾기", "기초 실습"]) {
      await nav.getByRole("link", { name: tab, exact: true }).click();
      await expect(nav.getByRole("link")).toHaveText(["기초 실습", "공동 실습", "넌스 찾기"]);
      await expect(nav.getByRole("link", { name: tab, exact: true })).toHaveAttribute("aria-current", "page");
      await expect(page.getByLabel("화면 테마", { exact: true })).toHaveValue(theme);
      await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
      const result = await page.evaluate(() => {
        const style = getComputedStyle(document.documentElement);
        return {
          tokens: ["--page-bg", "--surface", "--text", "--muted", "--accent", "--selected-bg"].map(key =>
            style.getPropertyValue(key).trim().replace(/^#([\da-f])([\da-f])([\da-f])$/i, "#$1$1$2$2$3$3")),
          overflow: document.documentElement.scrollWidth > innerWidth,
        };
      });
      expect(result.tokens).toEqual(tokens);
      expect(result.overflow).toBe(false);
      if (tab === "넌스 찾기") {
        await ready(page);
        await page.screenshot({ path: info.outputPath(`nonce-${theme}-390.png`), fullPage: true });
      }
    }
  });
}
