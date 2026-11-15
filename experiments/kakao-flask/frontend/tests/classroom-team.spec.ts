import { expect, test, type Page } from "./team-test";

async function start(page: Page, name: string, code?: string) {
  await page.goto(code ? `/?classroom=join&room=${code}` : "/?classroom=team");
  await page.getByLabel("별명", { exact: true }).fill(name);
  await page.locator("form").getByRole("button", { name: code ? "입장" : "팀 실습 시작", exact: true }).click();
  await expect(page.getByText("연결됨", { exact: true })).toBeVisible();
  return (await page.getByRole("heading", { name: /^방 / }).innerText()).split(" ")[1];
}

test("교사 없이 대표를 맡고 공동 조작하며 같은 링크와 쿠키로 기록을 이어간다", async ({ browser }) => {
  const contexts = await Promise.all(Array.from({ length: 3 }, () => browser.newContext()));
  const [a, b, returning] = await Promise.all(contexts.map((c) => c.newPage()));
  const errors: string[] = [];
  for (const page of [a, b, returning]) page.on("pageerror", (error) => errors.push(error.message));
  // Retain the real socket so this test can deliver an authoritative restart signal.
  await a.addInitScript(() => {
    const Original = window.WebSocket;
    window.WebSocket = class extends Original {
      constructor(url: string | URL, protocols?: string | string[]) {
        super(url, protocols);
        (window as unknown as { teamSocket: WebSocket }).teamSocket = this;
      }
    };
  });
  try {
    const code = await start(a, "팀원 A");
    await start(b, "팀원 B", code);
    await a.getByRole("button", { name: "팀 A 맡기", exact: true }).click();
    await expect(a.getByRole("heading", { name: /^팀 A · UTXO/ })).toBeVisible();
    await expect(b.getByRole("button", { name: "팀 A · 팀원 A", exact: true })).toBeDisabled();
    await b.getByRole("button", { name: "팀 B 맡기", exact: true }).click();
    for (const page of [a, b]) {
      await expect(page.getByRole("button", { name: "방 종료", exact: true })).toHaveCount(0);
      await expect(page.getByRole("button", { name: /참가자 내보내기|대표 지정/ })).toHaveCount(0);
    }

    // A lost old-generation mutation must be discarded instead of silently replayed.
    let draftRequests = 0;
    await a.route("**/api/education/classroom/rooms/*/commands", async (route) => {
      if (route.request().postDataJSON()?.action === "draft") {
        draftRequests++;
        await route.abort("failed");
      } else await route.continue();
    });
    await a.getByRole("button", { name: "1. 거래 작성", exact: true }).click();
    await expect(a.getByRole("button", { name: "같은 요청 결과 확인", exact: true })).toBeVisible();
    await a.evaluate(() => {
      (window as unknown as { teamSocket: WebSocket }).teamSocket.dispatchEvent(new MessageEvent("message", {
        data: JSON.stringify({ type: "ended", code: "UNAUTHORIZED", message: "서버가 재시작되었습니다" }),
      }));
    });
    await expect(a.getByRole("button", { name: "같은 요청 결과 확인", exact: true })).toHaveCount(0);
    await expect(a.getByRole("heading", { name: /^팀 A · UTXO/ })).toBeVisible();
    await expect(a.getByText("연결됨", { exact: true })).toBeVisible();
    expect(draftRequests).toBe(1);
    await a.unroute("**/api/education/classroom/rooms/*/commands");
    await a.getByRole("button", { name: "1. 거래 작성", exact: true }).click();
    await a.getByRole("button", { name: "2. 전자서명", exact: true }).click();
    await a.getByRole("button", { name: "3. 검증·전파", exact: true }).click();
    await expect(b.getByText("◷ Mempool 대기", { exact: false }).first()).toBeVisible();
    await expect(a.getByRole("combobox", { name: "관찰 거래", exact: true })).not.toHaveValue("");
    const transactionId = await a.getByRole("combobox", { name: "관찰 거래", exact: true }).inputValue();

    await b.getByRole("button", { name: "노드·블록체인", exact: true }).click();
    await b.getByLabel("메시지 자동 전달").click();
    await expect(b.getByLabel("메시지 자동 전달")).toBeChecked();
    await expect(b.getByText(/메시지 0개 대기/)).toBeVisible();
    await b.getByRole("button", { name: "채굴", exact: true }).click();
    await b.getByRole("button", { name: "세 후보 준비", exact: true }).click();
    await b.getByRole("button", { name: "내 채굴 참여", exact: true }).click();
    await b.getByRole("button", { name: "각자 한 번 계산", exact: true }).click();
    await expect(b.getByText(/후보 #1 · 시도 1회|채굴자 B · #1 · 1회/).first()).toBeVisible();

    // Starting again reuses the stable public room, including its ledger.
    await returning.goto(`/?classroom=join&room=${code}`);
    await expect(returning.getByLabel("별명", { exact: true })).toBeVisible();
    await returning.evaluate((roomCode) => {
      sessionStorage.setItem("team-blockchain-classroom", JSON.stringify({
        code: roomCode, runId: "legacy-run", participantId: "legacy-teacher", token: "expired-teacher-token",
      }));
    }, code);
    await returning.reload();
    await expect(returning.getByLabel("별명", { exact: true })).toBeVisible();
    await expect(returning.getByLabel("방 코드", { exact: true })).toHaveValue(code);
    await expect(returning.getByRole("alert")).toContainText("이름을 입력해 같은 방에 입장하세요");
    await expect(returning.getByRole("button", { name: "교사 방 만들기", exact: true })).toHaveCount(0);
    expect(await start(returning, "다시 온 팀원")).toBe(code);
    // New browsers choose an older confirmed transaction explicitly instead of completing a new guide from history.
    await returning.getByRole("combobox", { name: "관찰 거래", exact: true }).selectOption(transactionId);
    await expect(returning.getByRole("combobox", { name: "관찰 거래", exact: true })).toHaveValue(transactionId);
    await a.evaluate(() => {
      const key = "team-blockchain-classroom";
      const saved = JSON.parse(sessionStorage.getItem(key)!);
      sessionStorage.setItem(key, JSON.stringify({ ...saved, runId: "old-run", token: "expired-token" }));
    });
    await a.reload();
    await expect(a.getByText("연결됨", { exact: true })).toBeVisible();
    await expect(a.getByRole("heading", { name: new RegExp(`^방 ${code}$`) })).toBeVisible();
    await expect(a.getByRole("combobox", { name: "관찰 거래", exact: true })).toHaveValue(transactionId);
    await a.getByRole("button", { name: "지금 저장", exact: true }).click();
    await expect(a.getByText("저장됨 · 같은 링크로 이어가기", { exact: true })).toBeVisible();
    await a.getByRole("button", { name: "퇴장", exact: true }).click();
    await expect(a.getByLabel("방 코드", { exact: true })).toHaveValue(code);
    await expect(a).toHaveURL(new RegExp(`room=${code}`));
    await b.getByRole("button", { name: "대표 내려놓기", exact: true }).click();
    await expect(b.getByRole("button", { name: "팀 B 맡기", exact: true })).toBeEnabled();
    await returning.getByRole("button", { name: "채굴", exact: true }).click();
    await expect(returning.getByRole("button", { name: "세 후보 준비", exact: true })).toBeEnabled();
    expect(errors).toEqual([]);
  } finally {
    for (const context of contexts) await context.close();
  }
});
