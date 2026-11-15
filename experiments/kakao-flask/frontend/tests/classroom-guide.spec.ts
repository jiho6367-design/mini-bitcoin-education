import { expect, test, type Page } from "./team-test";
import type { RoomState } from "../src/ui/useClassroom";

async function enter(page: Page, name: string, code?: string) {
  await page.goto(code ? `/?classroom=join&room=${code}` : "/?classroom=team");
  await page.getByLabel("별명", { exact: true }).fill(name);
  await page.locator("form").getByRole("button", { name: code ? "입장" : "팀 실습 시작", exact: true }).click();
  await expect(page.getByText("연결됨", { exact: true })).toBeVisible();
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

const guide = (page: Page) => page.getByTestId("team-practice-guide");
const step = (page: Page, number: number) => guide(page).locator(`button[aria-label^="가이드 ${number}단계 "]`);
const progress = (page: Page) => guide(page).getByRole("progressbar", { name: "공통 실습 완료 단계" });
const target = (page: Page) => guide(page).getByRole("combobox", { name: "가이드 확인 거래", exact: true });
const nextAction = (page: Page) => guide(page).getByTestId("guide-next-action");
const navigate = (page: Page, name: string) => page.getByRole("navigation", { name: "공동 실습 화면", exact: true })
  .getByRole("button", { name, exact: true }).click();

async function expectComplete(page: Page, number: number, complete: boolean) {
  await expect(step(page, number)).toHaveAttribute("data-complete", String(complete));
}

async function automatic(page: Page, enabled: boolean) {
  await navigate(page, "노드·블록체인");
  const checkbox = page.getByLabel("메시지 자동 전달", { exact: true });
  if (await checkbox.isChecked() !== enabled) await checkbox.click();
  if (enabled) await expect(checkbox).toBeChecked();
  else await expect(checkbox).not.toBeChecked();
}

async function prepareMining(page: Page) {
  await navigate(page, "채굴");
  await page.getByRole("combobox", { name: "실습 방식", exact: true }).selectOption("guided");
  await page.getByRole("combobox", { name: "계산 속도", exact: true }).selectOption("fast");
  await page.getByRole("button", { name: "세 후보 준비", exact: true }).click();
  await page.getByRole("button", { name: "내 채굴 참여", exact: true }).click();
}

test("모바일 안내 이동은 완료 체크나 실습 명령을 대신 실행하지 않는다", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const code = await enter(page, "자동 가이드 관찰자");
  const initial = await roomState(page, code);
  let commands = 0;
  page.on("request", (request) => {
    if (request.method() === "POST" && request.url().endsWith("/commands")) commands += 1;
  });
  await expect(progress(page)).toHaveAttribute("aria-valuenow", "0");
  await expect(target(page)).toHaveValue("");
  await expect(guide(page)).toHaveAttribute("data-current-step", "1");
  await guide(page).getByRole("button", { name: "팀 선택으로 이동", exact: true }).click();
  await expect(page.locator("#team-role-selection")).toBeFocused();
  await step(page, 2).click();
  await guide(page).getByRole("button", { name: "전파 설정으로 이동", exact: true }).click();
  await expect(page.getByLabel("메시지 자동 전달", { exact: true })).not.toBeChecked();
  await step(page, 5).click();
  await guide(page).getByRole("button", { name: "채굴 화면으로 이동", exact: true }).click();
  await expect(page.getByRole("button", { name: "전체 시작 / 계속", exact: true })).toBeDisabled();
  await step(page, 6).click();
  await expect(progress(page)).toHaveAttribute("aria-valuenow", "0");
  for (let number = 1; number <= 6; number += 1) await expectComplete(page, number, false);
  await guide(page).getByRole("button", { name: "가이드 접기", exact: true }).click();
  await guide(page).getByRole("button", { name: "가이드 펼치기", exact: true }).click();
  await expect(guide(page)).toHaveAttribute("data-current-step", "6");
  await guide(page).getByRole("button", { name: "다음 할 일 보기", exact: true }).click();
  await expect(guide(page)).toHaveAttribute("data-current-step", "1");
  await expect(nextAction(page)).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  const after = await roomState(page, code);
  expect(commands).toBe(0);
  expect(after.automatic).toBe(false);
  expect(after.lab.race).toBeNull();
  expect(after.participants.every((participant) => participant.team === null)).toBe(true);
  expect(after.lab.nodes).toEqual(initial.lab.nodes);
  await guide(page).screenshot({ path: testInfo.outputPath("mobile-automatic-guide.png") });
  await page.getByRole("button", { name: "퇴장", exact: true }).click();
});

test("실제 선택 거래의 접수·전달·채굴·합의만 자동 완료로 표시한다", async ({ browser }, testInfo) => {
  const baseURL = testInfo.project.use.baseURL;
  const contexts = await Promise.all([browser.newContext({ baseURL }), browser.newContext({ baseURL })]);
  const [a, b] = await Promise.all(contexts.map((context) => context.newPage()));
  try {
    const code = await enter(a, "자동 가이드 A");
    await enter(b, "자동 가이드 B", code);
    await a.getByRole("button", { name: "팀 A 맡기", exact: true }).click();
    await b.getByRole("button", { name: "팀 B 맡기", exact: true }).click();
    await expectComplete(a, 1, true);
    await expectComplete(b, 1, true);
    await expectComplete(a, 3, false);

    // A real empty block is evidence of mining, but not of this guide's transaction.
    await automatic(a, true);
    await expectComplete(a, 2, true);
    await prepareMining(a);
    await a.getByRole("button", { name: "전체 시작 / 계속", exact: true }).click();
    await expect.poll(async () => Object.values((await roomState(a, code)).lab.nodes)
      .every((node) => node.chain.length === 2), { timeout: 20_000 }).toBe(true);
    await expect(target(a)).toHaveValue("");
    await expectComplete(a, 5, false);
    await expectComplete(a, 6, false);

    // Hold real propagation to distinguish A's accepted transaction from all-node acceptance.
    await automatic(a, false);
    await navigate(a, "거래");
    await a.getByRole("combobox", { name: "받는 팀", exact: true }).selectOption({ label: "팀 B" });
    await a.getByLabel("송금액", { exact: true }).fill("10");
    await a.getByLabel("수수료", { exact: true }).fill("1");
    await a.getByRole("button", { name: "1. 거래 작성", exact: true }).click();
    await expectComplete(a, 3, false);
    const signedResponse = a.waitForResponse((response) => response.url().endsWith("/commands")
      && response.request().postDataJSON()?.action === "sign");
    await a.getByRole("button", { name: "2. 전자서명", exact: true }).click();
    const signed = (await (await signedResponse).json()).signed as { id: string };
    await expectComplete(a, 3, false);
    await a.getByRole("button", { name: "3. 검증·전파", exact: true }).click();
    await expect(target(a)).toHaveValue(signed.id);
    await expect(target(b)).toHaveValue(signed.id);
    await expect(a.getByRole("combobox", { name: "관찰 거래", exact: true })).toHaveValue(signed.id);
    await expectComplete(a, 3, true);
    await expectComplete(a, 4, false);
    await expectComplete(a, 5, false);
    await expectComplete(a, 6, false);

    // Waiting for a new transaction must not silently reselect an old pending one on reload.
    await target(a).selectOption("");
    await expectComplete(a, 3, false);
    await a.reload();
    await expect(a.getByText("연결됨", { exact: true })).toBeVisible();
    await expect(target(a)).toHaveValue("");
    await expectComplete(a, 3, false);
    await target(a).selectOption(signed.id);
    await expectComplete(a, 3, true);

    await automatic(a, true);
    await expectComplete(a, 2, true);
    await expectComplete(a, 4, true);
    await expectComplete(b, 4, true);
    await a.reload();
    await expect(a.getByText("연결됨", { exact: true })).toBeVisible();
    await expect(target(a)).toHaveValue(signed.id);
    await expectComplete(a, 4, true);
    await expect(guide(a)).toHaveAttribute("data-current-step", "5");

    await prepareMining(a);
    await navigate(b, "채굴");
    await b.getByRole("button", { name: "내 채굴 참여", exact: true }).click();
    await a.getByRole("button", { name: "전체 시작 / 계속", exact: true }).click();
    await expect.poll(async () => Object.values((await roomState(a, code)).lab.nodes)
      .every((node) => node.chain.some((block) => block.transactions.some((transaction) => transaction.id === signed.id))),
    { timeout: 20_000 }).toBe(true);
    for (const page of [a, b]) {
      for (let number = 1; number <= 6; number += 1) await expectComplete(page, number, true);
      await expect(progress(page)).toHaveAttribute("aria-valuenow", "6");
    }
    const final = await roomState(a, code);
    const [alice, bob] = final.lab.wallets;
    for (const node of Object.values(final.lab.nodes)) {
      expect(node.balances[alice.address].confirmed).toBe(89);
      expect(node.balances[bob.address].confirmed).toBe(110);
      expect(node.chainValidation.valid).toBe(true);
    }
    await guide(a).screenshot({ path: testInfo.outputPath("desktop-completed-guide.png") });

    // A disconnected display labels the last evidence and never retries a mutation itself.
    let repeatedCommands = 0;
    b.on("request", (request) => {
      if (request.method() === "POST" && request.url().endsWith("/commands")) repeatedCommands += 1;
    });
    await contexts[1].setOffline(true);
    await expect(b.getByTestId("practice-controls")).toHaveAttribute("aria-busy", "true");
    await expect(guide(b)).toHaveAttribute("data-reliable", "false");
    await expect(target(b)).toBeDisabled();
    await expect(guide(b)).toContainText("연결 확인 중 마지막 수신 상태");
    await contexts[1].setOffline(false);
    await expect(b.getByTestId("practice-controls")).toHaveAttribute("aria-busy", "false");
    await expect(guide(b)).toHaveAttribute("data-reliable", "true");
    await expect(target(b)).toBeEnabled();
    await expect(progress(b)).toHaveAttribute("aria-valuenow", "6");
    expect(repeatedCommands).toBe(0);
    await a.getByRole("button", { name: "퇴장", exact: true }).click();
    await b.getByRole("button", { name: "퇴장", exact: true }).click();
  } finally {
    for (const context of contexts) await context.close();
  }
});

test("이전 완료 기록은 직접 선택할 때만 확인하고 새 거래 대기는 새로고침해도 유지한다", async ({ page }) => {
  const code = await enter(page, "이전 기록 확인자");
  const current = await roomState(page, code);
  const oldTransaction = current.lab.nodes.A.chain.flatMap((block) => block.transactions)
    .find((transaction) => !!transaction.signature && (!transaction.kind || transaction.kind === "regular"));
  expect(oldTransaction).toBeTruthy();
  let commands = 0;
  page.on("request", (request) => {
    if (request.method() === "POST" && request.url().endsWith("/commands")) commands += 1;
  });
  await expect(target(page)).toHaveValue("");
  for (let number = 3; number <= 6; number += 1) await expectComplete(page, number, false);
  await target(page).selectOption(oldTransaction!.id);
  for (let number = 3; number <= 6; number += 1) await expectComplete(page, number, true);
  await expect(page.getByRole("combobox", { name: "관찰 거래", exact: true })).toHaveValue(oldTransaction!.id);
  await page.reload();
  await expect(page.getByText("연결됨", { exact: true })).toBeVisible();
  await expect(target(page)).toHaveValue(oldTransaction!.id);
  await expectComplete(page, 6, true);
  await target(page).selectOption("");
  for (let number = 3; number <= 6; number += 1) await expectComplete(page, number, false);
  await expect(page.getByRole("combobox", { name: "관찰 거래", exact: true })).toHaveValue("");
  await page.reload();
  await expect(page.getByText("연결됨", { exact: true })).toBeVisible();
  await expect(target(page)).toHaveValue("");
  await expectComplete(page, 6, false);
  await page.getByRole("combobox", { name: "관찰 거래", exact: true }).selectOption(oldTransaction!.id);
  await expect(target(page)).toHaveValue(oldTransaction!.id);
  await expectComplete(page, 6, true);
  await page.getByRole("combobox", { name: "관찰 거래", exact: true }).selectOption("");
  await expect(target(page)).toHaveValue("");
  await expectComplete(page, 6, false);
  expect(commands).toBe(0);
  await page.getByRole("button", { name: "퇴장", exact: true }).click();
});
