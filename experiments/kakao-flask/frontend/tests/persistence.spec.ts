import { expect, test } from "./team-test";
import { readFile } from "node:fs/promises";

test("저장된 개인 실습을 열람·HTML 저장하고 새로고침 뒤 이어 한다", async ({
  page,
  request,
}, info) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const { lab } = await (
    await request.post("/api/education/labs", {
      data: { mode: "example", difficulty: 1 },
    })
  ).json();
  const pid = lab.storage.id;
  const title = `저장 검증 ${pid.slice(0, 8)}`;
  const drafted = await (
    await request.post(`/api/education/labs/${lab.labId}/nodes/A/drafts`, {
      data: {
        senderAddress: lab.wallets[0].address,
        recipientAddress: lab.wallets[1].address,
        amount: 30,
        fee: 1,
      },
    })
  ).json();
  const { signed } = await (
    await request.post(`/api/education/labs/${lab.labId}/wallets/sign`, {
      data: { draft: drafted.draft },
    })
  ).json();
  await request.post(`/api/education/labs/${lab.labId}/nodes/A/transactions`, {
    data: { transaction: signed },
  });
  await request.post(`/api/education/practices/${pid}/rename`, { data: { title } });
  await request.delete(`/api/education/labs/${lab.labId}`);
  await page.goto("/");
  await page.getByRole("button", { name: "저장된 실습", exact: true }).click();
  const row = page.locator(".archive-item").filter({ hasText: title });
  await row.getByRole("button", { name: "결과 보기", exact: true }).click();
  const result = page.frameLocator('iframe[title="저장된 실습 결과"]');
  await expect(
    result.getByRole("heading", { name: title, exact: true }),
  ).toBeVisible();
  await expect(
    result
      .getByText("입력 100 → Alice 69 + Bob 30 + 수수료 1", { exact: true })
      .or(
        result.getByText("입력 100 → Bob 30 + Alice 69 + 수수료 1", {
          exact: true,
        }),
      ),
  ).toBeVisible();
  const downloadEvent = page.waitForEvent("download");
  await row.getByRole("button", { name: "HTML로 저장" }).click();
  const download = await downloadEvent;
  const html = await readFile((await download.path())!, "utf8");
  expect(html).toContain(signed.id);
  expect(html).not.toContain("privateKey");
  expect(html).not.toContain("<script");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByLabel("화면 테마", { exact: true }).selectOption("dark");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: info.outputPath("saved-practice-mobile.png"),
    fullPage: true,
  });
  await row.getByRole("button", { name: "이어 하기", exact: true }).click();
  await expect(page.getByTestId("balance-0")).toHaveText("100코인");
  await page.reload();
  await page.getByRole("button", { name: "저장된 실습", exact: true }).click();
  await page
    .locator(".archive-item")
    .filter({ hasText: title })
    .getByRole("button", { name: "이어 하기", exact: true })
    .click();
  await expect(page.getByTestId("balance-0")).toHaveText("100코인");
  expect(errors).toEqual([]);
});

test("교사는 종료한 수업을 새 방 코드로 이어 연다", async ({
  page,
  request,
}) => {
  const secret =
    process.env.TEAM_TEACHER_SECRET || "classroom-test-only";
  const response = await request.post("/api/education/classroom/rooms", {
    data: { secret, name: "기록 교사" },
  });
  expect(response.ok()).toBeTruthy();
  const c = await response.json();
  await page.goto("/?classroom=teacher");
  await page.evaluate(
    (credential) =>
      sessionStorage.setItem(
        "team-blockchain-classroom",
        JSON.stringify(credential),
      ),
    c,
  );
  await page.reload();
  await expect(page.getByText("연결됨", { exact: true })).toBeVisible();
  const state = await (
    await request.get(`/api/education/classroom/rooms/${c.code}`, {
      headers: { Authorization: `Bearer ${c.token}` },
    })
  ).json();
  const title = `지난 수업 ${c.code}`;
  await request.post(`/api/education/classroom/practices/${state.storage.id}/rename`, {
    headers: { Authorization: `Bearer ${c.token}` },
    data: { title },
  });
  await request.post(`/api/education/classroom/rooms/${c.code}/commands`, {
    headers: { Authorization: `Bearer ${c.token}` },
    data: {
      runId: c.runId,
      commandId: crypto.randomUUID(),
      action: "close",
      payload: {},
    },
  });
  await page.evaluate(() => sessionStorage.clear());
  await page.reload();
  await page.getByLabel("교사용 비밀코드").fill(secret);
  await page.getByRole("button", { name: "저장된 수업", exact: true }).click();
  await page
    .locator(".archive-item")
    .filter({ hasText: title })
    .getByRole("button", { name: "이어 하기", exact: true })
    .click();
  await expect(page.getByText("연결됨", { exact: true })).toBeVisible();
  const next = await page.evaluate(() =>
    JSON.parse(sessionStorage.getItem("team-blockchain-classroom")!),
  );
  expect(next.code).not.toBe(c.code);
  expect(next.token).not.toBe(c.token);
  await request.post(`/api/education/classroom/rooms/${next.code}/commands`, {
    headers: { Authorization: `Bearer ${next.token}` },
    data: {
      runId: next.runId,
      commandId: crypto.randomUUID(),
      action: "close",
      payload: {},
    },
  });
});
