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
const step = (page: Page, number: number, title: string) =>
  guide(page).getByRole("button", { name: `가이드 ${number}단계 ${title}`, exact: true });

test("모바일에서 가이드 이동만으로 팀 역할이나 장부를 바꾸지 않고 순서를 따라간다", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const code = await enter(page, "가이드 관찰자");
  const initial = await roomState(page, code);
  let commands = 0;
  page.on("request", (request) => {
    if (request.method() === "POST" && request.url().endsWith("/commands")) commands += 1;
  });
  await expect(guide(page)).toHaveAttribute("data-current-step", "1");
  await expect(guide(page).getByRole("heading", { name: "1. 역할 선택", exact: true })).toBeVisible();
  await guide(page).getByRole("button", { name: "팀 선택으로 이동", exact: true }).click();
  await expect(page.locator("#team-role-selection")).toBeFocused();
  await expect(page.getByRole("button", { name: "팀 A 맡기", exact: true })).toBeEnabled();

  await guide(page).getByRole("button", { name: "다음 단계", exact: true }).click();
  await expect(guide(page)).toHaveAttribute("data-current-step", "2");
  await guide(page).getByRole("button", { name: "전파 설정으로 이동", exact: true }).click();
  await expect(page.getByLabel("메시지 자동 전달", { exact: true })).not.toBeChecked();
  await expect(page.getByText(/메시지 0개 대기/)).toBeVisible();
  await guide(page).getByRole("button", { name: "다음 단계", exact: true }).click();
  await guide(page).getByRole("button", { name: "거래 화면으로 이동", exact: true }).click();
  await expect(page.getByRole("heading", { name: "한 거래가 지금 어디에 있나요?", exact: true })).toBeVisible();
  await guide(page).getByRole("button", { name: "다음 단계", exact: true }).click();
  await expect(guide(page).getByRole("heading", { name: "4. 거래 전달 확인", exact: true })).toBeVisible();
  await guide(page).getByRole("button", { name: "다음 단계", exact: true }).click();
  await guide(page).getByRole("button", { name: "채굴 화면으로 이동", exact: true }).click();
  await expect(page.getByRole("button", { name: "세 후보 준비", exact: true })).toBeEnabled();
  await expect(page.getByRole("button", { name: "전체 시작 / 계속", exact: true })).toBeDisabled();
  await guide(page).getByRole("button", { name: "다음 단계", exact: true }).click();
  await expect(guide(page)).toHaveAttribute("data-current-step", "6");
  await expect(guide(page).getByRole("heading", { name: "6. 결과 확인", exact: true })).toBeVisible();
  await guide(page).getByRole("button", { name: "가이드 마치기", exact: true }).click();
  await expect(guide(page).getByRole("button", { name: "가이드 펼치기", exact: true })).toHaveAttribute("aria-expanded", "false");
  await guide(page).getByRole("button", { name: "가이드 펼치기", exact: true }).click();
  await expect(guide(page)).toHaveAttribute("data-current-step", "6");
  await guide(page).getByRole("button", { name: "이전 단계", exact: true }).click();
  await expect(guide(page)).toHaveAttribute("data-current-step", "5");
  await step(page, 1, "역할 선택").click();
  await expect(guide(page)).toHaveAttribute("data-current-step", "1");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  const after = await roomState(page, code);
  expect(commands).toBe(0);
  expect(after.automatic).toBe(false);
  expect(after.lab.race).toBeNull();
  expect(after.participants.every((participant) => participant.team === null)).toBe(true);
  expect(after.lab.nodes).toEqual(initial.lab.nodes);
  await guide(page).scrollIntoViewIfNeeded();
  await page.screenshot({ path: testInfo.outputPath("mobile-team-guide.png"), fullPage: true });
  await page.getByRole("button", { name: "퇴장", exact: true }).click();
});

test("팀원 둘이 화면 가이드대로 실제 송금과 채굴·전파를 확인한다", async ({ browser }, testInfo) => {
  const baseURL = testInfo.project.use.baseURL;
  const contexts = await Promise.all([browser.newContext({ baseURL }), browser.newContext({ baseURL })]);
  const [a, b] = await Promise.all(contexts.map((context) => context.newPage()));
  try {
    const code = await enter(a, "가이드 A");
    await enter(b, "가이드 B", code);
    await a.getByRole("button", { name: "팀 A 맡기", exact: true }).click();
    await b.getByRole("button", { name: "팀 B 맡기", exact: true }).click();
    await expect(a.getByRole("heading", { name: /^팀 A · UTXO/ })).toBeVisible();

    await step(a, 2, "자동 전달").click();
    await guide(a).getByRole("button", { name: "전파 설정으로 이동", exact: true }).click();
    await a.getByLabel("메시지 자동 전달", { exact: true }).click();
    await expect(a.getByLabel("메시지 자동 전달", { exact: true })).toBeChecked();

    await step(a, 3, "거래 만들기").click();
    await guide(a).getByRole("button", { name: "거래 화면으로 이동", exact: true }).click();
    await a.getByRole("combobox", { name: "받는 팀", exact: true }).selectOption({ label: "팀 B" });
    await a.getByLabel("송금액", { exact: true }).fill("10");
    await a.getByLabel("수수료", { exact: true }).fill("1");
    await a.getByRole("button", { name: "1. 거래 작성", exact: true }).click();
    const signedResponse = a.waitForResponse((response) => response.url().endsWith("/commands") && response.request().postDataJSON()?.action === "sign");
    await a.getByRole("button", { name: "2. 전자서명", exact: true }).click();
    const signed = (await (await signedResponse).json()).signed as { id: string };
    await a.getByRole("button", { name: "3. 검증·전파", exact: true }).click();
    await step(a, 4, "거래 전달 확인").click();
    await expect.poll(async () => {
      const current = await roomState(a, code);
      return Object.values(current.lab.nodes).every((node) => node.mempool.some((transaction) => transaction.id === signed.id));
    }).toBe(true);
    await expect(a.getByText("◷ Mempool 대기", { exact: false })).toHaveCount(3);

    await step(a, 5, "함께 채굴").click();
    await guide(a).getByRole("button", { name: "채굴 화면으로 이동", exact: true }).click();
    await a.getByRole("combobox", { name: "실습 방식", exact: true }).selectOption("guided");
    await a.getByRole("combobox", { name: "계산 속도", exact: true }).selectOption("fast");
    await a.getByRole("button", { name: "세 후보 준비", exact: true }).click();
    await a.getByRole("button", { name: "내 채굴 참여", exact: true }).click();
    await step(b, 5, "함께 채굴").click();
    await guide(b).getByRole("button", { name: "채굴 화면으로 이동", exact: true }).click();
    await b.getByRole("button", { name: "내 채굴 참여", exact: true }).click();
    await a.getByRole("button", { name: "전체 시작 / 계속", exact: true }).click();
    await expect.poll(async () => {
      const current = await roomState(a, code);
      return Object.values(current.lab.nodes).every((node) => node.chain.some((block) => block.transactions.some((transaction) => transaction.id === signed.id)));
    }, { timeout: 20_000 }).toBe(true);
    const final = await roomState(a, code);
    const [alice, bob] = final.lab.wallets;
    for (const node of Object.values(final.lab.nodes)) {
      expect(node.balances[alice.address].confirmed).toBe(89);
      expect(node.balances[bob.address].confirmed).toBe(110);
      expect(node.chainValidation.valid).toBe(true);
      expect(node.chain.at(-1)!.hash).toMatch(/^0[0-9a-f]{63}$/);
    }
    await step(a, 6, "결과 확인").click();
    await guide(a).getByRole("button", { name: "거래 화면으로 이동", exact: true }).click();
    await expect(a.getByText(/✓ 블록 #\d+ 포함/)).toHaveCount(3);
    await expect(guide(a)).toHaveAttribute("data-current-step", "6");
    await guide(a).scrollIntoViewIfNeeded();
    await a.screenshot({ path: testInfo.outputPath("desktop-team-guide-result.png"), fullPage: true });
    await a.getByRole("button", { name: "퇴장", exact: true }).click();
    await b.getByRole("button", { name: "퇴장", exact: true }).click();
  } finally {
    for (const context of contexts) await context.close();
  }
});
