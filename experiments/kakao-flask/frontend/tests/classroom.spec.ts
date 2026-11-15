import { expect, test, type Page } from "./team-test";

async function enter(page: Page, name: string, code?: string) {
  await page.goto("/?classroom=" + (code ? "join" : "teacher"));
  await page.getByLabel("별명", { exact: true }).fill(name);
  if (code) await page.getByLabel("방 코드", { exact: true }).fill(code);
  else
    await page
      .getByLabel("교사용 비밀코드")
      .fill(process.env.TEAM_TEACHER_SECRET || "classroom-test-only");
  await page
    .getByRole("button", { name: code ? "입장" : "방 생성", exact: true })
    .click();
  await expect(page.getByText("연결됨", { exact: true })).toBeVisible();
}

test("교사·세 대표·관찰자가 송금과 채굴 결과를 공유하고 재접속한다", async ({
  browser,
}, info) => {
  const contexts = await Promise.all(
    Array.from({ length: 5 }, () => browser.newContext()),
  );
  const pages = await Promise.all(contexts.map((c) => c.newPage()));
  const [teacher, a, b, c, observer] = pages;
  const errors: string[] = [];
  pages.forEach((p) => p.on("pageerror", (e) => errors.push(e.message)));
  try {
    await teacher.route("**/api/education/classroom/config", async (route) => {
      const response = await route.fetch();
      await route.fulfill({
        json: {
          ...(await response.json()),
          publicUrl: "https://classroom-test.trycloudflare.com",
        },
      });
    });
    await enter(teacher, "교사");
    await teacher
      .getByText("휴대폰 참여 · 링크 복사 / QR 코드", { exact: true })
      .click();
    await expect(teacher.getByAltText("참가자 접속 QR 코드")).toBeVisible();
    await expect(
      teacher.getByRole("link", { name: /https:\/\/classroom-test/ }),
    ).toHaveAttribute("href", /classroom=join&room=/);
    const code = (
      await teacher.getByRole("heading", { name: /^방 / }).innerText()
    ).split(" ")[1];
    for (const [i, p] of [a, b, c, observer].entries())
      await enter(p, ["대표A", "대표B", "대표C", "관찰자"][i], code);
    await teacher.getByText(/참가자 5\/30/).click();
    for (const n of ["A", "B", "C"])
      await teacher
        .getByRole("button", {
          name: `대표${n} 팀 ${n} 대표 지정`,
          exact: true,
        })
        .click();
    await expect(a.getByRole("heading", { name: /팀 A · UTXO/ })).toBeVisible();
    await expect(
      observer.getByRole("button", { name: "1. 거래 작성" }),
    ).toHaveCount(0);
    await a.getByLabel("UTXO 직접 선택").check();
    await a.getByLabel(/100 코인 사용 가능/).check();
    await a.getByRole("button", { name: "1. 거래 작성", exact: true }).click();
    await expect(a.getByText(/입력 100 → 팀 B 30/)).toBeVisible();
    await a.getByRole("button", { name: "2. 전자서명", exact: true }).click();
    await a.getByRole("button", { name: "3. 검증·전파", exact: true }).click();
    await expect(
      observer.getByText("◷ Mempool 대기", { exact: false }).first(),
    ).toBeVisible();
    await teacher
      .getByRole("button", { name: "노드·블록체인", exact: true })
      .click();
    await teacher.getByLabel("메시지 자동 전달").click();
    await expect(teacher.getByLabel("메시지 자동 전달")).toBeChecked();
    await expect(teacher.getByText(/메시지 0개 대기/)).toBeVisible();
    for (const p of pages)
      await p.getByRole("button", { name: "채굴", exact: true }).click();
    await teacher.getByLabel("계산 속도").selectOption("fast");
    await teacher
      .getByRole("button", { name: "세 후보 준비", exact: true })
      .click();
    for (const p of [a, b, c]) {
      await expect(
        p.getByRole("button", { name: "내 채굴 참여", exact: true }),
      ).toBeEnabled();
      await p
        .getByRole("button", { name: "내 채굴 참여", exact: true })
        .click();
    }
    await teacher
      .getByRole("button", { name: "전체 시작 / 계속", exact: true })
      .click();
    await expect(
      observer.getByText(/채굴자 [ABC] · #1 ·/).first(),
    ).toBeVisible();
    await expect(
      teacher.getByText("전달 대기 0개", { exact: false }),
    ).toBeVisible();
    await a.getByRole("button", { name: "거래", exact: true }).click();
    await a.getByLabel("UTXO 직접 선택").check();
    await expect(a.getByLabel(/100 코인/)).toHaveCount(0);
    await expect(a.getByLabel(/69 코인 사용 가능/)).toBeVisible();
    await a.reload();
    await expect(a.getByText("연결됨", { exact: true })).toBeVisible();
    await expect(a.getByRole("heading", { name: /팀 A · UTXO/ })).toBeVisible();
    await expect(a.getByText("69 코인 · 사용 가능 69")).toBeVisible();
    await expect(a.getByText("130 코인 · 사용 가능 130")).toBeVisible();
    await teacher.evaluate(() => window.scrollTo(0, 0));
    await teacher.screenshot({
      path: info.outputPath("classroom-race.png"),
      fullPage: true,
    });
    await observer.setViewportSize({ width: 390, height: 844 });
    await observer.emulateMedia({ reducedMotion: "reduce" });
    await observer.getByLabel("화면 테마").selectOption("dark");
    expect(
      await observer.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth + 1,
      ),
    ).toBe(true);
    await observer.evaluate(() => window.scrollTo(0, 0));
    await observer.screenshot({
      path: info.outputPath("classroom-mobile.png"),
      fullPage: true,
    });
    await teacher
      .getByRole("button", { name: "관찰자 팀 A 대표 지정", exact: true })
      .click();
    await expect(a.getByRole("heading", { name: /팀 A · UTXO/ })).toHaveCount(
      0,
    );
    expect(errors).toEqual([]);
    teacher.once("dialog", (d) => d.accept());
    await teacher.getByRole("button", { name: "방 종료", exact: true }).click();
    await expect(
      observer.getByText("수업 종료", { exact: true }),
    ).toBeVisible();
  } finally {
    for (const context of contexts) await context.close();
  }
});

test("참가 화면은 긴 교사용 비밀코드를 잘라 숨기지 않고 방 코드 안내를 보여준다", async ({
  page,
}) => {
  await page.goto("/?classroom=join");
  await page.getByLabel("별명", { exact: true }).fill("학생");
  await page
    .getByLabel("방 코드", { exact: true })
    .fill("teacher_secret_with_symbol");
  await expect(page.getByLabel("방 코드", { exact: true })).toHaveValue(
    "TEACHER_SECRET_WITH_SYMBOL",
  );
  await page.getByRole("button", { name: "입장", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText(
    "참가용 방 코드는 영문·숫자 8자리",
  );
  await page.goto("/?classroom=join&room=ABCD2345");
  await expect(page.getByLabel("방 코드", { exact: true })).toHaveValue(
    "ABCD2345",
  );
});

test("작성 상태는 화면 왕복에 유지되고 권한 교체 때 초기화되며 응답 유실은 같은 명령으로 확인한다", async ({ browser }) => {
  const teacherContext = await browser.newContext();
  const studentContext = await browser.newContext();
  const teacher = await teacherContext.newPage();
  const student = await studentContext.newPage();
  try {
    await enter(teacher, "복구 교사");
    const code = (await teacher.getByRole("heading", { name: /^방 / }).innerText()).split(" ")[1];
    await enter(student, "복구 학생", code);
    await teacher.getByText(/참가자 2\/30/).click();
    const assign = teacher.getByRole("button", { name: "복구 학생 팀 A 대표 지정", exact: true });
    await assign.click();
    await expect(student.getByRole("heading", { name: /팀 A · UTXO/ })).toBeVisible();
    await student.getByLabel("송금액", { exact: true }).fill("25");
    await student.getByLabel("수수료", { exact: true }).fill("2");
    await student.getByLabel("UTXO 직접 선택").check();
    await student.getByLabel(/100 코인 사용 가능/).check();
    await student.getByRole("button", { name: "1. 거래 작성", exact: true }).click();
    await expect(student.getByText(/입력 100 → 팀 B 25/)).toBeVisible();
    await student.getByRole("button", { name: "채굴", exact: true }).click();
    await student.getByRole("button", { name: "거래", exact: true }).click();
    await expect(student.getByLabel("송금액", { exact: true })).toHaveValue("25");
    await expect(student.getByLabel("수수료", { exact: true })).toHaveValue("2");
    await expect(student.getByLabel(/100 코인 사용 가능/)).toBeChecked();
    await expect(student.getByRole("button", { name: "2. 전자서명", exact: true })).toBeEnabled();
    await student.getByRole("button", { name: "2. 전자서명", exact: true }).click();
    await student.getByRole("button", { name: "활동 기록", exact: true }).click();
    await student.getByRole("button", { name: "거래", exact: true }).click();
    await expect(student.getByRole("button", { name: "3. 검증·전파", exact: true })).toBeEnabled();

    // Even reassignment to the same team invalidates its previous draft authority.
    await assign.click();
    await expect(student.getByRole("button", { name: "3. 검증·전파", exact: true })).toBeDisabled();
    await expect(student.getByLabel("송금액", { exact: true })).toHaveValue("30");
    await student.getByRole("button", { name: "1. 거래 작성", exact: true }).click();
    await student.getByRole("button", { name: "2. 전자서명", exact: true }).click();

    const submitBodies: { commandId: string }[] = [];
    await student.route("**/api/education/classroom/rooms/*/commands", async (route) => {
      const body = route.request().postDataJSON();
      if (body.action !== "submit") { await route.continue(); return; }
      submitBodies.push(body);
      if (submitBodies.length === 1) {
        await route.fetch(); // Server commits; the response is lost before the browser receives it.
        await route.abort();
      } else await route.continue();
    });
    await student.getByRole("button", { name: "3. 검증·전파", exact: true }).click();
    await expect(student.getByRole("button", { name: "같은 요청 결과 확인", exact: true })).toBeVisible();
    await expect(student.getByRole("button", { name: "1. 거래 작성", exact: true })).toBeDisabled();
    await student.getByRole("button", { name: "같은 요청 결과 확인", exact: true }).click();
    await expect(student.getByText(/◷ Mempool 대기/).first()).toBeVisible();
    await expect(student.getByRole("button", { name: "같은 요청 결과 확인", exact: true })).toHaveCount(0);
    expect(submitBodies).toHaveLength(2);
    expect(submitBodies[1].commandId).toBe(submitBodies[0].commandId);

    await teacher.getByRole("button", { name: "복구 학생 참가자 내보내기", exact: true }).click();
    await teacher.getByRole("button", { name: "내보내기 확인", exact: true }).click();
    await expect(teacher.getByText(/참가자 1\/30/)).toBeVisible();
    await expect(student.getByText("수업 종료", { exact: true })).toBeVisible();
    const secret = process.env.TEAM_TEACHER_SECRET || "classroom-test-only";
    const before = await teacher.request.get("/api/education/classroom/practices", { headers: { Authorization: `Bearer ${secret}` } });
    const records = (await before.json()).practices;
    teacher.once("dialog", (dialog) => dialog.accept());
    await teacher.getByRole("button", { name: "방 종료", exact: true }).click();
    await expect(teacher.getByRole("heading", { name: "교사 방 만들기", exact: true })).toBeVisible();
    const after = await teacher.request.get("/api/education/classroom/practices", { headers: { Authorization: `Bearer ${secret}` } });
    const saved = (await after.json()).practices;
    expect(records.every((record: { id: string }) => saved.some((item: { id: string }) => item.id === record.id))).toBe(true);
  } finally {
    await teacherContext.close();
    await studentContext.close();
  }
});
