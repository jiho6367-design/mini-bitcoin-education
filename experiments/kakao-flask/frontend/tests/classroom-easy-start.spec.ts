import { expect, test, type Page } from "./team-test";
import type { RoomState } from "../src/ui/useClassroom";

async function enter(page: Page, name: string, code?: string) {
  await page.goto(code ? `/?classroom=join&room=${code}` : "/?classroom=join");
  await page.getByLabel("별명", { exact: true }).fill(name);
  await page.locator("form").getByRole("button", { name: "기존 실습에 입장", exact: true }).click();
  await expect(page.getByText("연결됨", { exact: true })).toBeVisible();
  return (await page.getByRole("heading", { name: /^방 / }).innerText()).split(" ")[1];
}

async function create(page: Page, name: string, amount = 100) {
  await page.goto("/?classroom=join");
  await page.getByRole("button", { name: "새 실습 만들기", exact: true }).click();
  await page.getByLabel("별명", { exact: true }).fill(name);
  await page.getByLabel("팀별 초기 코인", { exact: true }).fill(String(amount));
  await page.locator("form").getByRole("button", { name: "새 실습 시작", exact: true }).click();
  await expect(page.getByText("연결됨", { exact: true })).toBeVisible();
  return (await page.getByRole("heading", { name: /^방 / }).innerText()).split(" ")[1];
}

async function state(page: Page, code: string): Promise<RoomState> {
  const token = await page.evaluate(() => JSON.parse(sessionStorage.getItem("team-blockchain-classroom")!).token as string);
  const response = await page.request.get(`/api/education/classroom/rooms/${code}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  expect(response.ok()).toBe(true);
  return response.json();
}

const easy = (page: Page) => page.getByTestId("team-easy-start");
const guide = (page: Page) => page.getByTestId("team-practice-guide");

test("입장은 한 가지 기본 행동으로 안내하고 중복 통합 실습 버튼을 없앤다", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/?classroom=join");
  await expect(page.getByRole("heading", { name: "공동 실습", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "통합 실습", exact: true })).toHaveCount(0);
  await expect(page.getByRole("navigation", { name: "실습 모드", exact: true }).getByRole("link", { name: "통합 실습", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "팀 실습 시작", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "공동 실습 참여", exact: true })).toHaveCount(0);
  await expect(page.locator("form").getByRole("button", { name: /^(기존 실습에 입장|실습 시작)$/ })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath("mobile-default-entry.png") });
  await page.getByRole("button", { name: "새 실습 만들기", exact: true }).click();
  await expect(page.getByLabel("팀별 초기 코인", { exact: true })).toHaveValue("100");
  await expect(page.locator("form").getByRole("button", { name: "새 실습 시작", exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath("mobile-new-practice-entry.png") });
});

test("별명은 같은 브라우저 재입장과 변경 뒤에도 실제 참가자 이름으로 보인다", async ({ browser }, testInfo) => {
  const contexts = await Promise.all([browser.newContext({ baseURL: testInfo.project.use.baseURL }), browser.newContext({ baseURL: testInfo.project.use.baseURL })]);
  const [a, b] = await Promise.all(contexts.map((context) => context.newPage()));
  try {
    const code = await create(a, "처음 별명");
    await enter(b, "함께 보는 팀원", code);
    const initial = await state(a, code);
    const participantId = initial.participants.find((person) => person.name === "처음 별명")!.id;
    await expect(a.getByTestId("team-identity")).toContainText("내 별명 · 처음 별명");
    await expect(b.getByTestId("team-identity").locator('[aria-label="접속 중인 참가자"]')).toContainText("처음 별명");
    await a.getByRole("button", { name: "퇴장", exact: true }).click();
    await a.getByLabel("별명", { exact: true }).fill("다시 입력한 별명");
    await a.locator("form").getByRole("button", { name: "기존 실습에 입장", exact: true }).click();
    await expect(a.getByText("연결됨", { exact: true })).toBeVisible();
    await expect.poll(async () => (await state(b, code)).participants.find((person) => person.id === participantId)?.name).toBe("다시 입력한 별명");
    await expect(b.getByTestId("team-identity").locator('[aria-label="접속 중인 참가자"]')).toContainText("다시 입력한 별명");
    await a.getByRole("button", { name: "별명 변경", exact: true }).click();
    await a.getByLabel("새 별명", { exact: true }).fill("저장한 별명");
    await a.getByRole("button", { name: "별명 저장", exact: true }).click();
    await expect.poll(async () => (await state(b, code)).participants.find((person) => person.id === participantId)?.name).toBe("저장한 별명");
    await expect(b.getByTestId("team-identity").locator('[aria-label="접속 중인 참가자"]')).toContainText("저장한 별명");
    await a.reload();
    await expect(a.getByText("연결됨", { exact: true })).toBeVisible();
    const final = await state(a, code);
    expect(final.participants.find((person) => person.id === participantId)?.name).toBe("저장한 별명");
    expect(final.participants).toHaveLength(initial.participants.length);
    expect(final.lab.nodes).toEqual(initial.lab.nodes);
    await a.screenshot({ path: testInfo.outputPath("desktop-visible-nicknames.png") });
  } finally {
    for (const context of contexts) await context.close();
  }
});

test("혼자 다음 행동을 눌러 송금과 실제 단일 노드 채굴을 끝까지 진행한다", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const code = await create(page, "혼자 실습하는 지호");
  await expect(easy(page)).toBeVisible();
  // The easy flow still requires explicit clicks; opening it cannot change the ledger.
  const initial = await state(page, code);
  expect(initial.participants).toHaveLength(1);
  expect(initial.participants[0].team).toBeNull();
  expect(initial.lab.race).toBeNull();
  await easy(page).getByRole("button", { name: "팀 A로 시작", exact: true }).click();
  await expect.poll(async () => (await state(page, code)).participants[0].team).toBe("A");
  await easy(page).getByRole("button", { name: "자동 전달 켜기", exact: true }).click();
  await expect.poll(async () => (await state(page, code)).automatic).toBe(true);
  await easy(page).getByRole("combobox", { name: "연습 받는 팀", exact: true }).selectOption({ label: "팀 B" });
  await easy(page).getByLabel("연습 송금액", { exact: true }).fill("10");
  await easy(page).getByLabel("연습 수수료", { exact: true }).fill("1");
  await easy(page).getByRole("button", { name: "송금 내용 확인", exact: true }).click();
  await easy(page).getByRole("button", { name: "전자서명하기", exact: true }).click();
  await easy(page).getByRole("button", { name: "거래 보내기", exact: true }).click();
  await expect(page.getByTestId("wallet-spendable-A")).toHaveText("0");
  await expect(page.getByTestId("wallet-reserved-A")).toHaveText("100");
  await expect(easy(page)).toContainText(/대기 거래|채굴/);
  await expect.poll(async () => Object.values((await state(page, code)).lab.nodes).every((node) => node.mempool.length === 1)).toBe(true);
  // A reserved balance after reload must lead to the existing transaction's
  // mining action, rather than demanding another unaffordable transfer.
  await page.reload();
  await expect(page.getByText("연결됨", { exact: true })).toBeVisible();
  await expect(page.getByTestId("wallet-spendable-A")).toHaveText("0");
  await expect(easy(page).getByRole("button", { name: "송금 내용 확인", exact: true })).toHaveCount(0);
  await expect(easy(page).getByRole("button", { name: "채굴 준비", exact: true })).toBeEnabled();
  await easy(page).screenshot({ path: testInfo.outputPath("mobile-reserved-continue-mining.png") });
  // Root's direct next action will prepare/start the real single-node job.
  await easy(page).getByRole("button", { name: "채굴 준비", exact: true }).click();
  await expect.poll(async () => (await state(page, code)).lab.race?.singleNode).toBe("A");
  await easy(page).getByRole("button", { name: "채굴 시작", exact: true }).click();
  await expect.poll(async () => guide(page).getByRole("progressbar", { name: "공통 실습 완료 단계", exact: true }).getAttribute("aria-valuenow"), { timeout: 20_000 }).toBe("6");
  const final = await state(page, code);
  expect(final.participants).toHaveLength(1);
  expect(final.participants[0].team).toBe("A");
  expect(final.lab.race?.singleNode).toBe("A");
  expect(final.lab.race?.jobs.B.attempts).toBe(0);
  expect(final.lab.race?.jobs.C.attempts).toBe(0);
  for (const node of Object.values(final.lab.nodes)) {
    expect(node.chainValidation.valid).toBe(true);
    expect(node.balances[final.lab.wallets[0].address].confirmed).toBe(89);
    expect(node.balances[final.lab.wallets[1].address].confirmed).toBe(110);
  }
  await easy(page).getByRole("button", { name: "결과 보기", exact: true }).click();
  await expect(page.getByTestId("wallet-confirmed-A")).toHaveText("89");
  await expect(page.getByTestId("wallet-spendable-A")).toHaveText("89");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await easy(page).screenshot({ path: testInfo.outputPath("mobile-solo-complete.png") });
  await easy(page).getByRole("button", { name: "다음 송금 연습", exact: true }).click();
  await expect(easy(page).getByRole("button", { name: "송금 내용 확인", exact: true })).toBeVisible();
  await expect(easy(page)).toContainText("89코인");
  expect((await state(page, code)).lab.nodes).toEqual(final.lab.nodes);
});
