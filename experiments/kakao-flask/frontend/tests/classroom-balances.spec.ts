import { expect, test, type Page } from "./team-test";
import type { NodeId } from "../src/api/types";
import type { RoomState } from "../src/ui/useClassroom";

async function enter(page: Page, name: string, code?: string) {
  await page.goto(code ? `/?classroom=join&room=${code}` : "/?classroom=team");
  await page.getByLabel("별명", { exact: true }).fill(name);
  await page.locator("form").getByRole("button", { name: /^(기존 실습에 입장|실습 시작)$/ }).click();
  await expect(page.getByText("연결됨", { exact: true })).toBeVisible();
  await page.getByText("직접 조작 · 팀 선택과 거래·채굴 상세", { exact: true }).click();
  return (await page.getByRole("heading", { name: /^방 / }).innerText()).split(" ")[1];
}

async function roomState(page: Page, code: string): Promise<RoomState> {
  const token = await page.evaluate(() => JSON.parse(sessionStorage.getItem("team-blockchain-classroom")!).token as string);
  const response = await page.request.get(`/api/education/classroom/rooms/${code}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  expect(response.ok()).toBe(true);
  return response.json();
}

const balances = (page: Page) => page.getByTestId("team-wallet-balances");
const card = (page: Page, team: NodeId) => balances(page).locator(`[data-team="${team}"]`);
const navigate = (page: Page, name: string) => page.getByRole("navigation", { name: "공동 실습 화면", exact: true })
  .getByRole("button", { name, exact: true }).click();

async function numbers(page: Page, team: NodeId, confirmed: number, spendable: number, reserved: number) {
  await expect(page.getByTestId(`wallet-confirmed-${team}`)).toHaveText(String(confirmed));
  await expect(page.getByTestId(`wallet-spendable-${team}`)).toHaveText(String(spendable));
  await expect(page.getByTestId(`wallet-reserved-${team}`)).toHaveText(String(reserved));
}

test("모바일에서 고정 배정과 코인 예약 설명을 읽어도 장부를 변경하지 않는다", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const code = await enter(page, "잔액 안내 관찰자");
  const initial = await roomState(page, code);
  let commands = 0;
  page.on("request", (request) => {
    if (request.method() === "POST" && request.url().endsWith("/commands")) commands += 1;
  });
  await expect(balances(page).getByText("시작 코인 · 팀마다 100코인", { exact: true })).toBeVisible();
  for (const team of ["A", "B", "C"] as const) await numbers(page, team, 100, 100, 0);
  const explanation = balances(page).locator("details");
  await explanation.getByText("잔액이 있는데 사용 가능이 0인 이유", { exact: true }).click();
  await expect(explanation).toHaveAttribute("open", "");
  await expect(explanation).toContainText("100");
  await expect(explanation).toContainText("10");
  await expect(explanation).toContainText("89");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  expect(commands).toBe(0);
  expect((await roomState(page, code)).lab.nodes).toEqual(initial.lab.nodes);
  await balances(page).screenshot({ path: testInfo.outputPath("mobile-initial-allocation.png") });
  await page.getByRole("button", { name: "퇴장", exact: true }).click();
});

test("서명만으로 예약하지 않고 접수된 입력 전체를 예약한 뒤 실제 채굴로 거스름돈을 복구한다", async ({ browser }, testInfo) => {
  const baseURL = testInfo.project.use.baseURL;
  const contexts = await Promise.all([browser.newContext({ baseURL, viewport: { width: 390, height: 844 } }), browser.newContext({ baseURL })]);
  const [a, b] = await Promise.all(contexts.map((context) => context.newPage()));
  try {
    const code = await enter(a, "잔액 A");
    await enter(b, "잔액 B", code);
    await a.getByRole("button", { name: "팀 A 맡기", exact: true }).click();
    await b.getByRole("button", { name: "팀 B 맡기", exact: true }).click();
    for (const team of ["A", "B", "C"] as const) await numbers(a, team, 100, 100, 0);
    await a.getByRole("combobox", { name: "받는 팀", exact: true }).selectOption({ label: "팀 B" });
    await a.getByLabel("송금액", { exact: true }).fill("10");
    await a.getByLabel("수수료", { exact: true }).fill("1");
    await a.getByRole("button", { name: "1. 거래 작성", exact: true }).click();
    await expect(a.getByRole("button", { name: "2. 전자서명", exact: true })).toBeEnabled();
    await numbers(a, "A", 100, 100, 0);
    const signedResponse = a.waitForResponse((response) => response.url().endsWith("/commands")
      && response.request().postDataJSON()?.action === "sign");
    await a.getByRole("button", { name: "2. 전자서명", exact: true }).click();
    const signed = (await (await signedResponse).json()).signed as { id: string };
    await expect(a.getByRole("button", { name: "3. 검증·전파", exact: true })).toBeEnabled();
    await numbers(a, "A", 100, 100, 0);
    await a.getByRole("button", { name: "3. 검증·전파", exact: true }).click();
    await numbers(a, "A", 100, 0, 100);
    await numbers(a, "B", 100, 100, 0);
    await expect(card(a, "A")).toContainText("대기 거래가 코인 입력을 예약해 새 송금에 사용할 수 없습니다");
    const pending = await roomState(a, code);
    const alice = pending.lab.wallets[0];
    expect(pending.lab.nodes.A.mempool.some((transaction) => transaction.id === signed.id)).toBe(true);
    expect(pending.lab.nodes.B.balances[alice.address].spendable).toBe(100);
    expect(pending.lab.nodes.A.reserved.map((key) => pending.lab.nodes.A.utxos[key].amount)).toEqual([100]);
    await balances(a).locator("details").getByText("잔액이 있는데 사용 가능이 0인 이유", { exact: true }).click();
    expect(await a.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await balances(a).screenshot({ path: testInfo.outputPath("mobile-reserved-input.png") });

    await navigate(a, "노드·블록체인");
    await a.getByLabel("메시지 자동 전달", { exact: true }).click();
    await expect(a.getByLabel("메시지 자동 전달", { exact: true })).toBeChecked();
    await expect.poll(async () => Object.values((await roomState(a, code)).lab.nodes)
      .every((node) => node.mempool.some((transaction) => transaction.id === signed.id))).toBe(true);
    await navigate(a, "채굴");
    await a.getByRole("combobox", { name: "실습 방식", exact: true }).selectOption("guided");
    await a.getByRole("combobox", { name: "계산 속도", exact: true }).selectOption("fast");
    await a.getByRole("button", { name: "세 후보 준비", exact: true }).click();
    await a.getByRole("button", { name: "내 채굴 참여", exact: true }).click();
    await a.getByRole("button", { name: "전체 시작 / 계속", exact: true }).click();
    await expect.poll(async () => Object.values((await roomState(a, code)).lab.nodes)
      .every((node) => node.chain.some((block) => block.transactions.some((transaction) => transaction.id === signed.id))),
    { timeout: 20_000 }).toBe(true);
    await navigate(a, "거래");
    await numbers(a, "A", 89, 89, 0);
    await numbers(a, "B", 110, 110, 0);
    await numbers(a, "C", 100, 100, 0);
    await expect(card(a, "A")).not.toContainText("대기 거래가 코인 입력을 예약해 새 송금에 사용할 수 없습니다");
    const final = await roomState(a, code);
    expect(Object.values(final.lab.nodes).every((node) => node.chainValidation.valid)).toBe(true);
    await a.setViewportSize({ width: 1440, height: 1000 });
    await balances(a).screenshot({ path: testInfo.outputPath("confirmed-change-balances.png") });
    // A later transaction reserves the actual 89-coin change input, not the original allocation.
    await a.getByLabel("송금액", { exact: true }).fill("5");
    await a.getByRole("button", { name: "1. 거래 작성", exact: true }).click();
    await a.getByRole("button", { name: "2. 전자서명", exact: true }).click();
    await a.getByRole("button", { name: "3. 검증·전파", exact: true }).click();
    await numbers(a, "A", 89, 0, 89);
    await expect(card(a, "A")).toContainText("대기 거래가 코인 입력을 예약해 새 송금에 사용할 수 없습니다");
    await a.getByRole("button", { name: "퇴장", exact: true }).click();
    await b.getByRole("button", { name: "퇴장", exact: true }).click();
  } finally {
    for (const context of contexts) await context.close();
  }
});

test("새 참가자와 새로고침은 팀에 100코인을 추가 지급하거나 저장 잔액을 초기화하지 않는다", async ({ page, browser }, testInfo) => {
  const code = await enter(page, "기록을 이어 보는 팀원");
  const before = await roomState(page, code);
  await numbers(page, "A", 89, 0, 89);
  await numbers(page, "B", 110, 110, 0);
  await numbers(page, "C", 100, 100, 0);
  const visitor = await browser.newContext({ baseURL: testInfo.project.use.baseURL });
  try {
    const other = await visitor.newPage();
    await enter(other, "새 관찰자", code);
    await numbers(other, "A", 89, 0, 89);
    await numbers(other, "B", 110, 110, 0);
    await page.reload();
    await expect(page.getByText("연결됨", { exact: true })).toBeVisible();
    await numbers(page, "A", 89, 0, 89);
    await numbers(page, "B", 110, 110, 0);
    await numbers(page, "C", 100, 100, 0);
    const after = await roomState(page, code);
    expect(after.lab.wallets).toEqual(before.lab.wallets);
    expect(after.lab.nodes).toEqual(before.lab.nodes);
    await other.getByRole("button", { name: "퇴장", exact: true }).click();
    await page.getByRole("button", { name: "퇴장", exact: true }).click();
  } finally {
    await visitor.close();
  }
});
