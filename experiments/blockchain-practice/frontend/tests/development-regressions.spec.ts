import { expect, test, type APIRequestContext, type Page } from "./team-test";
import type { DraftResult, LabSnapshot, MiningResult, Transaction } from "../src/api/types";

async function send(request: APIRequestContext, lab: LabSnapshot, amount: number) {
  const draft: DraftResult = await (await request.post(`/api/education/labs/${lab.labId}/nodes/A/drafts`, {
    data: { senderAddress: lab.wallets[0].address, recipientAddress: lab.wallets[1].address, amount, fee: 1 },
  })).json();
  const { signed }: { signed: Transaction } = await (await request.post(`/api/education/labs/${lab.labId}/wallets/sign`, { data: { draft: draft.draft } })).json();
  const submitted: { lab: LabSnapshot } = await (await request.post(`/api/education/labs/${lab.labId}/nodes/A/transactions`, { data: { transaction: signed } })).json();
  return { signed, lab: submitted.lab };
}

async function saveAndOpen(page: Page, request: APIRequestContext, lab: LabSnapshot) {
  const title = `개발 회귀 ${lab.storage!.id}`;
  await request.post(`/api/education/practices/${lab.storage!.id}/rename`, { data: { title } });
  await request.delete(`/api/education/labs/${lab.labId}`);
  await page.goto("/advanced");
  await page.getByRole("button", { name: "저장된 실습", exact: true }).click();
  await page.getByLabel("저장 기록 검색", { exact: true }).fill(title);
  await page.getByRole("button", { name: "검색", exact: true }).click();
  const item = page.locator(".archive-item").filter({ hasText: title });
  await expect(item).toContainText("일반 거래");
  await item.getByRole("button", { name: "이어 하기", exact: true }).click();
}

test("지갑 추가 뒤에도 선택한 채굴자 주소와 작성 거래의 수신자가 유지된다", async ({ page }) => {
  await page.goto("/advanced");
  await page.getByRole("button", { name: /예제 실습 시작/ }).click();
  const receiver = page.getByRole("combobox", { name: "받는 지갑", exact: true });
  await receiver.selectOption({ label: "채굴자 A" });
  const address = await receiver.inputValue();
  await page.getByRole("button", { name: "1. 거래 작성", exact: true }).click();
  await page.getByRole("button", { name: "＋ 지갑 추가", exact: true }).click();
  await expect(receiver).toHaveValue(address);
  await page.getByRole("button", { name: "2. 전자서명", exact: true }).click();
  const submitted = page.waitForResponse(response => response.request().method() === "POST" && /\/nodes\/A\/transactions$/.test(response.url()));
  await page.getByRole("button", { name: "3. 검증·전파", exact: true }).click();
  const result: { lab: LabSnapshot } = await (await submitted).json();
  expect(result.lab.nodes.A.mempool[0].outputs[0].address).toBe(address);
});

test("이어 하기에서 두 거래를 복구하고 관찰 선택을 안내·잔액 요약과 함께 바꾼다", async ({ page, request }) => {
  let { lab }: { lab: LabSnapshot } = await (await request.post("/api/education/labs", { data: { mode: "example", difficulty: 1 } })).json();
  const first = await send(request, lab, 30);
  lab = first.lab;
  let mined: MiningResult = await (await request.post(`/api/education/labs/${lab.labId}/mining`, { data: { nodeId: "A", mode: "fast", step: false } })).json();
  for (let i = 0; i < 100 && mined.mining.status !== "found"; i++) {
    mined = await (await request.post(`/api/education/labs/${lab.labId}/mining/${mined.mining.jobId}/tick`)).json();
  }
  expect(mined.mining.status).toBe("found");
  lab = mined.lab!;
  while (lab.queue.length) lab = (await (await request.post(`/api/education/labs/${lab.labId}/messages/deliver-next`)).json()).lab;
  const second = await send(request, lab, 5);
  await saveAndOpen(page, request, second.lab);
  await expect(page.getByTestId("flow-3")).toHaveAttribute("data-state", "current");
  const observed = page.getByRole("combobox", { name: "관찰 거래", exact: true });
  await expect(observed.locator("option")).toHaveCount(2);
  await observed.selectOption(first.signed.id);
  await expect(page.getByTestId("flow-6")).toHaveAttribute("data-state", "current");
  await expect(page.locator(".lesson-recap")).toContainText("30 코인");
  await expect(page.getByTestId("output-state").filter({ hasText: "다음 거래에 예약됨" })).toHaveCount(1);
  await observed.selectOption(second.signed.id);
  await expect(page.getByTestId("flow-3")).toHaveAttribute("data-state", "current");
  await expect(page.locator(".lesson-recap")).toHaveCount(0);
});

test("복원된 자유·빠른 경쟁을 그대로 표시하고 다음 후보 속도와 구분한다", async ({ page, request }) => {
  let { lab }: { lab: LabSnapshot } = await (await request.post("/api/education/labs", { data: { mode: "example", difficulty: 1 } })).json();
  ({ lab } = await (await request.post(`/api/education/labs/${lab.labId}/race`, { data: { action: "prepare", guided: false, mode: "fast" } })).json());
  await saveAndOpen(page, request, lab);
  await expect(page.getByRole("combobox", { name: "실습 방식", exact: true })).toHaveValue("free");
  await page.getByRole("navigation", { name: "실습 화면" }).getByRole("button", { name: /^채굴/ }).click();
  const speed = page.getByRole("combobox", { name: "계산 속도", exact: true });
  await expect(speed).toHaveValue("fast");
  await speed.selectOption("learning");
  await expect(page.getByText(/현재 계산 속도: 빠르게/)).toContainText("새로 준비하기 전까지는 현재 속도");
});

test("보관함의 현지 날짜 변경은 첫 페이지와 결과 수를 함께 갱신한다", async ({ page, request }) => {
  const prefix = `페이지 날짜 ${Date.now()}`;
  for (let i = 0; i < 21; i++) {
    const { lab }: { lab: LabSnapshot } = await (await request.post("/api/education/labs", { data: { mode: "manual" } })).json();
    await request.post(`/api/education/practices/${lab.storage!.id}/rename`, { data: { title: `${prefix}-${i}` } });
    await request.delete(`/api/education/labs/${lab.labId}`);
  }
  await page.goto("/advanced");
  await page.getByRole("button", { name: "저장된 실습", exact: true }).click();
  await page.getByLabel("저장 기록 검색").fill(prefix);
  await page.getByRole("button", { name: "검색", exact: true }).click();
  await expect(page.locator(".archive-item")).toHaveCount(20);
  await page.getByRole("button", { name: "다음 기록", exact: true }).click();
  await expect(page.locator(".archive-item")).toHaveCount(1);
  const tomorrow = await page.evaluate(() => {
    const day = new Date();
    day.setDate(day.getDate() + 1);
    return `${day.getFullYear()}-${String(day.getMonth() + 1).padStart(2, "0")}-${String(day.getDate()).padStart(2, "0")}`;
  });
  await page.getByLabel("저장 날짜 검색").fill(tomorrow);
  await expect(page.getByText("검색 결과 0개 · 0–0번째", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "이전 기록", exact: true })).toBeDisabled();
  await page.getByLabel("저장 날짜 검색").fill("");
  await expect(page.locator(".archive-item")).toHaveCount(20);
  await expect(page.getByText("검색 결과 21개 · 1–20번째", { exact: true })).toBeVisible();
});
