import { expect, test, type Page } from "./team-test";
import type { NodeId } from "../src/api/types";
import type { RoomState } from "../src/ui/useClassroom";

async function enter(page: Page, name: string, code?: string) {
  await page.goto(code ? `/?classroom=join&room=${code}` : "/?classroom=team");
  await page.getByLabel("별명", { exact: true }).fill(name);
  await page.locator("form").getByRole("button", { name: code ? "입장" : "팀 실습 시작", exact: true }).click();
  await expect(page.getByText("연결됨", { exact: true })).toBeVisible();
  return (await page.getByRole("heading", { name: /^방 / }).innerText()).split(" ")[1];
}

async function create(page: Page, name: string, amount: number) {
  await page.goto("/?classroom=team");
  await page.getByRole("button", { name: "새 실습 만들기", exact: true }).click();
  await page.getByLabel("별명", { exact: true }).fill(name);
  await page.getByLabel("팀별 초기 코인", { exact: true }).fill(String(amount));
  await page.locator("form").getByRole("button", { name: "새 실습 시작", exact: true }).click();
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

const navigate = (page: Page, name: string) => page.getByRole("navigation", { name: "공동 실습 화면", exact: true })
  .getByRole("button", { name, exact: true }).click();

async function initialBalances(page: Page, amount: number) {
  for (const team of ["A", "B", "C"] as const) {
    await expect(page.getByTestId(`wallet-confirmed-${team}`)).toHaveText(String(amount));
    await expect(page.getByTestId(`wallet-spendable-${team}`)).toHaveText(String(amount));
    await expect(page.getByTestId(`wallet-reserved-${team}`)).toHaveText("0");
  }
}

test("새 실습의 초기 금액을 선택해도 기존 기본 링크와 장부는 유지한다", async ({ browser }, testInfo) => {
  const contexts = await Promise.all(Array.from({ length: 4 }, () => browser.newContext({ baseURL: testInfo.project.use.baseURL })));
  const [original, small, large, returning] = await Promise.all(contexts.map((context) => context.newPage()));
  try {
    const defaultCode = await enter(original, "기존 기본 실습");
    const originalState = await roomState(original, defaultCode);
    await initialBalances(original, 100);

    await small.setViewportSize({ width: 390, height: 844 });
    const smallCode = await create(small, "초기 50 실습", 50);
    const largeCode = await create(large, "초기 250 실습", 250);
    expect(new Set([defaultCode, smallCode, largeCode]).size).toBe(3);
    await initialBalances(small, 50);
    await initialBalances(large, 250);
    const smallState = await roomState(small, smallCode);
    const largeState = await roomState(large, largeCode);
    expect(smallState.lab.genesis.allocations?.map((allocation) => allocation.amount)).toEqual([50, 50, 50]);
    expect(largeState.lab.genesis.allocations?.map((allocation) => allocation.amount)).toEqual([250, 250, 250]);
    expect(smallState.lab.genesis.hash).not.toBe(largeState.lab.genesis.hash);
    expect((await roomState(original, defaultCode)).lab.nodes).toEqual(originalState.lab.nodes);
    const config = await original.request.get("/api/education/classroom/config");
    expect((await config.json()).defaultTeamCode).toBe(defaultCode);
    expect(await small.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await small.getByTestId("team-wallet-balances").screenshot({ path: testInfo.outputPath("mobile-custom-initial-coins.png") });

    await small.reload();
    await expect(small.getByText("연결됨", { exact: true })).toBeVisible();
    await initialBalances(small, 50);
    expect((await roomState(small, smallCode)).lab.nodes).toEqual(smallState.lab.nodes);
    expect(await enter(returning, "기존 링크 이어가기")).toBe(defaultCode);
    await initialBalances(returning, 100);
    expect((await roomState(returning, defaultCode)).lab.nodes).toEqual(originalState.lab.nodes);
    await expect(returning).toHaveURL(new RegExp(`room=${defaultCode}`));
    // The in-room shortcut leaves only this participant and opens a distinct setup.
    await returning.getByRole("button", { name: "새 실습 만들기", exact: true }).click();
    await expect(returning.getByLabel("팀별 초기 코인", { exact: true })).toBeVisible();
    await returning.getByLabel("팀별 초기 코인", { exact: true }).fill("25");
    await returning.locator("form").getByRole("button", { name: "새 실습 시작", exact: true }).click();
    await expect(returning.getByText("연결됨", { exact: true })).toBeVisible();
    await initialBalances(returning, 25);
    expect((await roomState(original, defaultCode)).lab.nodes).toEqual(originalState.lab.nodes);
  } finally {
    for (const context of contexts) await context.close();
  }
});

test("선택한 한 노드만 실제 채굴하고 다른 노드는 받은 블록을 검증한다", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const code = await create(page, "단일 채굴 실습", 75);
  const initial = await roomState(page, code);
  expect(initial.participants.every((person) => person.team === null)).toBe(true);
  let commands = 0;
  page.on("request", (request) => {
    if (request.method() === "POST" && request.url().endsWith("/commands")) commands += 1;
  });
  await navigate(page, "채굴");
  await page.getByRole("button", { name: "한 노드 채굴", exact: true }).click();
  await page.getByRole("button", { name: "채굴 경쟁", exact: true }).click();
  await expect(page.getByRole("button", { name: "세 후보 준비", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "한 노드 채굴", exact: true }).click();
  await page.getByRole("combobox", { name: "채굴할 노드", exact: true }).selectOption("B");
  await page.getByRole("combobox", { name: "계산 속도", exact: true }).selectOption("fast");
  expect(commands).toBe(0);
  expect((await roomState(page, code)).lab.nodes).toEqual(initial.lab.nodes);
  expect((await roomState(page, code)).lab.race).toBeNull();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.getByRole("button", { name: "선택 노드 후보 준비", exact: true }).click();
  let state = await roomState(page, code);
  expect(state.lab.race?.singleNode).toBe("B");
  expect(state.lab.race?.enabled).toEqual({ A: false, B: true, C: false });
  await page.getByRole("button", { name: "한 번 계산", exact: true }).click();
  state = await roomState(page, code);
  expect(state.lab.race?.jobs.B.attempts).toBe(1);
  expect(state.lab.race?.jobs.A.attempts).toBe(0);
  expect(state.lab.race?.jobs.C.attempts).toBe(0);
  if (!state.lab.race?.completed.length) {
    await page.getByRole("button", { name: "채굴 시작 / 계속", exact: true }).click();
  }
  await expect.poll(async () => (await roomState(page, code)).lab.race?.completed.length, { timeout: 20_000 }).toBe(1);
  state = await roomState(page, code);
  const mined = state.lab.race!.completed[0];
  expect(mined.node).toBe("B");
  expect(state.lab.nodes.B.chain.at(-1)?.hash).toBe(mined.hash);
  expect(state.lab.nodes.A.chain).toHaveLength(1);
  expect(state.lab.nodes.C.chain).toHaveLength(1);
  for (const team of ["A", "C"] as NodeId[]) expect(state.lab.race?.jobs[team].attempts).toBe(0);
  const rewardAddress = state.lab.minerWallets!.B.address;
  expect(state.lab.nodes.B.balances[rewardAddress]).toMatchObject({ confirmed: 50, spendable: 0, immature: 50 });
  await page.screenshot({ path: testInfo.outputPath("mobile-single-node-found.png") });

  await navigate(page, "노드·블록체인");
  await page.getByLabel("메시지 자동 전달", { exact: true }).click();
  await expect(page.getByLabel("메시지 자동 전달", { exact: true })).toBeChecked();
  await expect.poll(async () => Object.values((await roomState(page, code)).lab.nodes)
    .every((node) => node.chain.at(-1)?.hash === mined.hash && node.chainValidation.valid), { timeout: 15_000 }).toBe(true);
  const final = await roomState(page, code);
  await page.reload();
  await expect(page.getByText("연결됨", { exact: true })).toBeVisible();
  await initialBalances(page, 75);
  expect((await roomState(page, code)).lab.nodes).toEqual(final.lab.nodes);
  await navigate(page, "채굴");
  await expect(page.getByRole("combobox", { name: "채굴할 노드", exact: true })).toHaveValue("B");
  expect((await roomState(page, code)).lab.race?.singleNode).toBe("B");
});
