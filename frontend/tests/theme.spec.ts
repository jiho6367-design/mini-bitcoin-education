import { readFileSync } from "node:fs";
import { expect, test, type Page } from "@playwright/test";
import manifest from "../package.json" with { type: "json" };
import lockfile from "../package-lock.json" with { type: "json" };

const themeKey = "mini-bitcoin-theme";
type Preference = "system" | "light" | "dark";
const navigate = (page: Page, title: string) =>
  page
    .getByRole("navigation")
    .getByRole("button", { name: new RegExp(title) })
    .click();

async function selectTheme(
  page: Page,
  preference: Preference,
  resolved = preference,
) {
  await page.getByLabel("화면 테마", { exact: true }).selectOption(preference);
  await expect(page.locator("html")).toHaveAttribute(
    "data-theme-preference",
    preference,
  );
  if (resolved !== "system")
    await expect(page.locator("html")).toHaveAttribute("data-theme", resolved);
}

async function start(page: Page, difficulty = "1") {
  await page.goto("/");
  await page
    .getByLabel("실습 난이도", { exact: true })
    .selectOption(difficulty);
  await page
    .getByRole("button", { name: /^예제 실습 시작/ })
    .click();
  // 실습은 백엔드의 응답을 받은 뒤에 표시된다.
  await expect(page.getByTestId("balance-0")).toBeVisible();
}

async function submit(page: Page) {
  for (const name of ["1. 거래 작성", "2. 전자서명", "3. 검증·전파"])
    await page.getByRole("button", { name, exact: true }).click();
  await expect(page.getByTestId("input-state")).toHaveText(
    "대기 거래에 예약됨",
  );
}

async function walletData(page: Page) {
  return page.locator(".wallet-card > .hash").allTextContents();
}

// 실제 계산의 확률과 무관하게 App이 같은 채굴 작업을 유지하는지 검사한다.
// 채굴 API만 정해진 응답으로 대신하고 나머지 요청은 실제 백엔드가 처리한다.
// 실제 백엔드 계산과 테마 전환은 아래 별도 테스트에서 검사한다.
async function trackMining(page: Page) {
  const audit = { started: 0, jobIds: [] as string[], commands: [] as string[] };
  const job = {
    jobId: "",
    seq: 0,
    status: "paused",
    mode: "fast",
    node: "A",
    attempts: 0,
  };
  await page.route("**/api/labs/*/mining**", async (route) => {
    const request = route.request();
    // /api/labs/{labId}/mining[/{jobId}/{command}]
    const [, , , , , jobId, command] = new URL(request.url()).pathname.split(
      "/",
    );
    if (!command) {
      const body = request.postDataJSON() as {
        nodeId: string;
        mode: string;
        step: boolean;
      };
      audit.started++;
      audit.commands.push("start");
      Object.assign(job, {
        jobId: `tracked-${audit.started}`,
        seq: 1,
        status: body.step ? "paused" : "running",
        mode: body.mode,
        node: body.nodeId,
        attempts: 1,
      });
      audit.jobIds.push(job.jobId);
    } else {
      audit.commands.push(command);
      if (jobId !== job.jobId)
        return route.fulfill({
          status: 409,
          json: { error: { code: "STALE_JOB", message: "이전 작업" } },
        });
      job.seq++;
      if (command === "tick" && job.status === "running") job.attempts++;
      if (command === "step" && job.status === "paused") job.attempts++;
      if (command === "pause" && job.status === "running")
        job.status = "paused";
      if (command === "resume" && job.status === "paused") {
        job.status = "running";
        job.attempts++;
      }
      if (command === "stop") job.status = "stopped";
    }
    return route.fulfill({
      json: {
        mining: {
          jobId: job.jobId,
          seq: job.seq,
          status: job.status,
          node: job.node,
          mode: job.mode,
          attempts: job.attempts,
          elapsedMs: job.attempts,
          observationMs: job.attempts * 250,
          samples: [],
          block: {
            sessionId: "1".repeat(64),
            height: 1,
            previousHash: "0".repeat(64),
            transactionsHash: "2".repeat(64),
            timestamp: 1000,
            difficulty: 4,
            nonce: job.attempts - 1,
            transactions: [],
            hash: "f".repeat(64),
          },
          message: null,
          nextTickInMs: job.status === "running" ? 60 : null,
        },
        lab: null,
      },
    });
  });
  return audit;
}

test("처음은 시스템 테마이고 OS 변경은 시스템 선택에만 반영된다", async ({
  page,
}) => {
  await page.emulateMedia({ colorScheme: "dark" });
  await page.goto("/");
  await expect(page.getByLabel("화면 테마", { exact: true })).toHaveValue(
    "system",
  );
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.emulateMedia({ colorScheme: "light" });
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await selectTheme(page, "dark");
  await page.emulateMedia({ colorScheme: "dark" });
  await page.emulateMedia({ colorScheme: "light" });
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await selectTheme(page, "light");
  await page.emulateMedia({ colorScheme: "dark" });
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await selectTheme(page, "system", "dark");
  await page.emulateMedia({ colorScheme: "light" });
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
});

test("선택만 저장하고 새로고침 시 테마는 유지하되 키·지갑·체인은 초기화한다", async ({
  page,
}) => {
  await page.emulateMedia({ colorScheme: "light" });
  await start(page);
  const addresses = await walletData(page);
  await submit(page);
  for (const preference of ["light", "dark", "system"] as const) {
    await selectTheme(
      page,
      preference,
      preference === "system" ? "light" : preference,
    );
    await expect(page.getByTestId("balance-0")).toHaveText("100코인");
    expect(await walletData(page)).toEqual(addresses);
    const stored = await page.evaluate(() =>
      Object.fromEntries(
        Object.keys(localStorage).map((key) => [
          key,
          localStorage.getItem(key),
        ]),
      ),
    );
    expect(stored).toEqual({ [themeKey]: preference });
  }
  await selectTheme(page, "dark");
  await page.reload();
  await expect(page.getByLabel("화면 테마", { exact: true })).toHaveValue(
    "dark",
  );
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await expect(
    page.getByRole("button", { name: /^예제 실습 시작/ }),
  ).toBeVisible();
  await expect(page.getByTestId("balance-0")).toHaveCount(0);
  await page
    .getByRole("button", { name: /^예제 실습 시작/ })
    .click();
  await expect(page.getByTestId("balance-0")).toHaveText("100코인");
  expect(await walletData(page)).not.toEqual(addresses);
  await navigate(page, "노드·블록체인");
  await expect(
    page.getByRole("heading", { name: "전달 대기열 · 0", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".block-tile")).toHaveCount(1);
});

test("잘못된 저장값과 저장소 접근 오류에서도 시스템 테마와 수동 선택이 동작한다", async ({
  browser,
}) => {
  for (const storage of ["invalid", "unavailable"]) {
    const context = await browser.newContext({
      colorScheme: "dark",
      baseURL: "http://127.0.0.1:4173",
    });
    try {
      const page = await context.newPage();
      await page.addInitScript(
        ({ storage, key }) => {
          if (storage === "invalid") localStorage.setItem(key, "unknown-theme");
          else {
            for (const method of ["getItem", "setItem"])
              Object.defineProperty(Storage.prototype, method, {
                value() {
                  throw new Error("storage unavailable");
                },
              });
          }
        },
        { storage, key: themeKey },
      );
      const errors: string[] = [];
      page.on("pageerror", (error) => errors.push(error.message));
      await page.goto("/");
      await expect(page.getByLabel("화면 테마", { exact: true })).toHaveValue(
        "system",
      );
      await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
      await selectTheme(page, "light");
      await page
        .getByRole("button", { name: /^예제 실습 시작/ })
        .click();
      await expect(page.getByTestId("balance-0")).toHaveText("100코인");
      expect(errors).toEqual([]);
    } finally {
      await context.close();
    }
  }
});

test("테마 전환은 작성·서명·검증·예약 UTXO와 확정 체인을 보존한다", async ({
  page,
}) => {
  await start(page);
  const addresses = await walletData(page);
  await page.getByLabel("송금액").fill("30");
  await page.getByRole("button", { name: "1. 거래 작성", exact: true }).click();
  const trace = await page.locator(".transaction-trace").textContent();
  await selectTheme(page, "dark");
  await expect(page.getByTestId("flow-1")).toHaveAttribute(
    "data-state",
    "current",
  );
  await expect(page.locator(".transaction-trace")).toHaveText(trace!);
  await page.getByRole("button", { name: "2. 전자서명", exact: true }).click();
  // 서명은 백엔드의 응답을 받은 뒤에 화면에 반영된다.
  await expect(page.getByTestId("learning-status")).toContainText(
    "전자서명 완료",
  );
  const signedStatus = await page.getByTestId("learning-status").textContent();
  await selectTheme(page, "light");
  await expect(page.getByTestId("learning-status")).toHaveText(signedStatus!);
  await expect(page.getByTestId("flow-2")).toHaveAttribute(
    "data-state",
    "current",
  );
  await expect(
    page.getByRole("button", { name: "2. 전자서명", exact: true }),
  ).toBeDisabled();
  await page.getByRole("button", { name: "3. 검증·전파", exact: true }).click();
  await expect(page.getByTestId("learning-status")).toContainText("검증 통과");
  const approvedStatus = await page
    .getByTestId("learning-status")
    .textContent();
  await selectTheme(page, "dark");
  await expect(page.getByTestId("learning-status")).toHaveText(approvedStatus!);
  await expect(page.getByTestId("input-state")).toHaveText(
    "대기 거래에 예약됨",
  );
  await expect(page.getByTestId("output-state").first()).toHaveText(
    "대기 중 · 아직 사용 불가",
  );
  expect(await walletData(page)).toEqual(addresses);
  await navigate(page, "채굴");
  await page
    .getByRole("button", { name: "연속 채굴 시작", exact: true })
    .click();
  await expect(page.getByText("✓ 채굴 성공 ·", { exact: false })).toBeVisible();
  await navigate(page, "노드·블록체인");
  await page.getByRole("checkbox", { name: "메시지 자동 전달" }).check();
  await expect(
    page.getByRole("heading", { name: "전달 대기열 · 0", exact: true }),
  ).toBeVisible();
  const hashes = await page.locator(".three-col .hash").allTextContents();
  await selectTheme(page, "light");
  expect(await page.locator(".three-col .hash").allTextContents()).toEqual(
    hashes,
  );
  await expect(page.locator(".block-tile")).toHaveCount(2);
  for (const id of ["A", "B", "C"]) {
    await page.getByLabel("관찰 노드", { exact: true }).selectOption(id);
    await navigate(page, "지갑·거래");
    await expect(page.getByTestId("balance-0")).toHaveText("70코인");
    await expect(page.getByTestId("balance-1")).toHaveText("30코인");
    expect(await walletData(page)).toEqual(addresses);
    await navigate(page, "노드·블록체인");
  }
});

const attemptCount = async (page: Page) =>
  Number(
    (await page.getByTestId("attempts").textContent())!.replaceAll(",", ""),
  );

test("실행 중·일시정지 중 테마를 바꿔도 앱의 채굴 작업과 작업 ID를 유지한다", async ({
  page,
}) => {
  const audit = await trackMining(page);
  const ticks = () => audit.commands.filter((c) => c === "tick").length;
  const controls = () => audit.commands.filter((c) => c !== "tick");
  await start(page, "4");
  await submit(page);
  await navigate(page, "채굴");
  await page
    .getByRole("button", { name: "천천히 학습 실행", exact: true })
    .click();
  await expect(page.getByText("● 계산 중", { exact: true })).toBeVisible();
  const attempts = await attemptCount(page);
  await selectTheme(page, "dark");
  await expect(page.getByText("● 계산 중", { exact: true })).toBeVisible();
  await expect.poll(() => attemptCount(page)).toBeGreaterThan(attempts);
  expect(controls()).toEqual(["start"]);
  await page.getByRole("button", { name: "일시정지", exact: true }).click();
  await expect(page.getByText("● 일시정지", { exact: true })).toBeVisible();
  const pausedAttempts = await page.getByTestId("attempts").textContent();
  const pausedNonce = await page.getByTestId("nonce").textContent();
  // 일시정지 요청과 엇갈린 마지막 계산 요청이 끝난 뒤의 횟수를 기준으로 삼는다.
  await page.waitForTimeout(150);
  const pausedTicks = ticks();
  await selectTheme(page, "light");
  await selectTheme(page, "system");
  await page.waitForTimeout(150);
  await expect(page.getByTestId("attempts")).toHaveText(pausedAttempts!);
  await expect(page.getByTestId("nonce")).toHaveText(pausedNonce!);
  await expect(page.getByText("● 일시정지", { exact: true })).toBeVisible();
  // 테마 전환은 새 작업을 만들거나 중지하지 않고, 일시정지 중에는 계산을 요청하지 않는다.
  expect(ticks()).toBe(pausedTicks);
  expect(controls()).toEqual(["start", "pause"]);
  expect(audit.started).toBe(1);
  expect(audit.jobIds).toHaveLength(1);
  await page.getByRole("button", { name: "계속 채굴", exact: true }).click();
  await expect(page.getByText("● 계산 중", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "채굴 중지", exact: true }).click();
  await expect(page.getByText("● 중지됨", { exact: true })).toBeVisible();
  expect(controls()).toEqual(["start", "pause", "resume", "stop"]);
  const stoppedTicks = ticks();
  await page.waitForTimeout(150);
  expect(ticks()).toBe(stoppedTicks);
});

test("실제 백엔드의 Nonce 계산은 테마 전환 뒤에도 계속되고 일시정지는 유지된다", async ({
  page,
}) => {
  await start(page, "4");
  await submit(page);
  await navigate(page, "채굴");
  await page
    .getByRole("button", { name: "천천히 학습 실행", exact: true })
    .click();
  await expect(page.getByTestId("mining-sample").first()).toBeVisible();
  const pause = page.getByRole("button", { name: "일시정지", exact: true });
  // 난이도 4의 첫 계산이 우연히 성공하면 그 실제 성공을 그대로 인정한다.
  if (!(await pause.isVisible())) {
    await expect(
      page.getByText("✓ 채굴 성공 ·", { exact: false }),
    ).toBeVisible();
    return;
  }
  const attempts = await attemptCount(page);
  await selectTheme(page, "dark");
  await expect.poll(() => attemptCount(page)).toBeGreaterThan(attempts);
  await pause.click();
  await expect(page.getByText("● 일시정지", { exact: true })).toBeVisible();
  const pausedAttempts = await page.getByTestId("attempts").textContent();
  const pausedNonce = await page.getByTestId("nonce").textContent();
  const pausedHash = await page.getByTestId("mining-hash").textContent();
  await selectTheme(page, "light");
  await page.waitForTimeout(300);
  await expect(page.getByTestId("attempts")).toHaveText(pausedAttempts!);
  await expect(page.getByTestId("nonce")).toHaveText(pausedNonce!);
  await expect(page.getByTestId("mining-hash")).toHaveText(pausedHash!);
  await expect(page.getByText("● 일시정지", { exact: true })).toBeVisible();
  // 화면의 현재 Nonce·해시는 백엔드가 실제로 계산한 가장 최근 표본이다.
  expect(pausedHash).toMatch(/^[0-9a-f]{64}$/);
  const latest = page.getByTestId("mining-sample").first();
  await expect(latest).toContainText(`Nonce ${pausedNonce}`);
  await expect(latest.getByTestId("sample-hash")).toHaveText(pausedHash!);
  await expect(page.getByText(/학습 실행 · 약 250ms/)).toBeVisible();
  await page.getByRole("button", { name: "채굴 중지", exact: true }).click();
  await expect(page.getByText("● 중지됨", { exact: true })).toBeVisible();
});

test("버전은 package.json·잠금 파일·화면이 일치하고 하단 문구가 정확하다", async ({
  page,
}) => {
  expect(lockfile.version).toBe(manifest.version);
  expect(lockfile.packages[""].version).toBe(manifest.version);
  // 화면과 백엔드는 같은 버전으로 함께 배포한다.
  const pyproject = readFileSync(
    new URL("../../backend/pyproject.toml", import.meta.url),
    "utf8",
  );
  expect(/^version = "(.+)"$/m.exec(pyproject)?.[1]).toBe(manifest.version);
  const health = await page.request.get("/api/health");
  expect(await health.json()).toEqual({
    status: "ok",
    version: manifest.version,
  });
  await page.goto("/");
  await expect(page.getByTestId("app-version")).toHaveText(
    `Mini Bitcoin v${manifest.version}`,
  );
  await expect(page.locator(".sidebar-footer small")).toHaveText(
    "학습용 블록체인",
  );
  await expect(
    page.getByText("배우기 위한 작은 블록체인", { exact: true }),
  ).toHaveCount(0);
});

// 계산된 실제 글자색과 투명 배경을 부모까지 합성해 핵심 학습 데이터의 대비를 검사한다.
async function checkReadability(page: Page) {
  const failures = await page.evaluate(() => {
    type Color = [number, number, number, number];
    const parse = (value: string): Color => {
      const values = value.match(/[\d.]+/g)?.map(Number) ?? [];
      return [values[0] ?? 0, values[1] ?? 0, values[2] ?? 0, values[3] ?? 1];
    };
    const over = (foreground: Color, background: Color): Color =>
      [
        ...[0, 1, 2].map(
          (i) =>
            foreground[i] * foreground[3] + background[i] * (1 - foreground[3]),
        ),
        1,
      ] as Color;
    const background = (element: Element): Color => {
      const layers: Color[] = [];
      for (
        let current: Element | null = element;
        current;
        current = current.parentElement
      ) {
        const color = parse(getComputedStyle(current).backgroundColor);
        layers.unshift(color);
        if (color[3] === 1) break;
      }
      return layers.reduce((result, color) => over(color, result), [
        255, 255, 255, 1,
      ] as Color);
    };
    const luminance = (color: Color) =>
      color
        .slice(0, 3)
        .map((channel) => {
          const n = channel / 255;
          return n <= 0.04045 ? n / 12.92 : ((n + 0.055) / 1.055) ** 2.4;
        })
        .reduce(
          (sum, value, i) => sum + value * [0.2126, 0.7152, 0.0722][i],
          0,
        );
    const contrast = (foreground: Color, bg: Color) => {
      const a = luminance(over(foreground, bg));
      const b = luminance(bg);
      return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
    };
    const visible = (element: Element) => {
      const rect = element.getBoundingClientRect();
      const style = getComputedStyle(element);
      return (
        rect.width > 0 &&
        rect.height > 0 &&
        style.visibility !== "hidden" &&
        style.display !== "none"
      );
    };
    const selectors = [
      "h1",
      "h2",
      "h3",
      "p",
      "label",
      "input",
      "select",
      "button:not(:disabled)",
      ".hash",
      ".result",
      ".checks strong",
      ".checks small",
      ".learning-status",
      ".trace-card strong",
      ".trace-card span",
      ".trace-card code",
      ".trace-arrow",
      ".diagnostics li",
      ".live-hash",
      ".live-hash mark",
      ".hash-sample code",
      ".sample-result",
      ".target-row code",
      ".status-pill",
    ];
    const errors: string[] = [];
    for (const element of document.querySelectorAll(selectors.join(","))) {
      if (
        !visible(element) ||
        (!element.textContent?.trim() && !(element instanceof HTMLInputElement))
      )
        continue;
      const style = getComputedStyle(element);
      const large =
        parseFloat(style.fontSize) >= 24 ||
        (parseFloat(style.fontSize) >= 18.66 &&
          Number(style.fontWeight) >= 700);
      const ratio = contrast(parse(style.color), background(element));
      if (ratio + 0.01 < (large ? 3 : 4.5))
        errors.push(
          `${element.tagName}.${element.className}: ${ratio.toFixed(2)} ${(element.textContent || "input").trim().slice(0, 45)}`,
        );
    }
    for (const svg of document.querySelectorAll(".network-diagram svg")) {
      const bg = background(svg);
      for (const text of svg.querySelectorAll("text")) {
        const group = text.parentElement;
        const circle = group?.querySelector("circle");
        const rect = group?.querySelector("rect");
        const y = Number(text.getAttribute("y"));
        const behind =
          circle &&
          Math.abs(y - Number(circle.getAttribute("cy"))) <
            Number(circle.getAttribute("r"))
            ? parse(getComputedStyle(circle).fill)
            : rect
              ? parse(getComputedStyle(rect).fill)
              : bg;
        const ratio = contrast(parse(getComputedStyle(text).fill), behind);
        if (ratio + 0.01 < 4.5)
          errors.push(`SVG text: ${ratio.toFixed(2)} ${text.textContent}`);
      }
      for (const path of svg.querySelectorAll("path[data-testid]")) {
        const style = getComputedStyle(path);
        const ratio = contrast(parse(style.stroke), bg);
        if (ratio + 0.01 < 3)
          errors.push(
            `SVG route: ${ratio.toFixed(2)} ${path.getAttribute("data-testid")}`,
          );
        const marker = svg.querySelector(
          `[id="${style.markerEnd.match(/#([^")]+)/)?.[1]}"] path`,
        );
        if (!marker || getComputedStyle(marker).fill !== style.stroke)
          errors.push("SVG marker must use the same color as its route");
      }
    }
    for (const element of document.querySelectorAll("svg [fill], svg [stroke]"))
      for (const attribute of ["fill", "stroke"])
        if (/^#[\da-f]{3,8}$/i.test(element.getAttribute(attribute) ?? ""))
          errors.push(
            `Hardcoded SVG ${attribute}: ${element.getAttribute(attribute)}`,
          );
    return errors;
  });
  expect(failures).toEqual([]);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
}

for (const theme of ["light", "dark"] as const) {
  test(`${theme} 테마에서 여섯 화면·UTXO·검증·실제 해시·노드 화살표를 확인한다`, async ({
    page,
  }, testInfo) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.goto("/");
    await selectTheme(page, theme);
    const capture = async (screen: string) => {
      await checkReadability(page);
      await page.evaluate(() => window.scrollTo(0, 0));
      await page.screenshot({
        path: testInfo.outputPath(`theme-${theme}-${screen}.png`),
        fullPage: true,
      });
    };
    await capture("home");
    await page.getByLabel("실습 난이도", { exact: true }).selectOption("1");
    await page
      .getByRole("button", { name: /^예제 실습 시작/ })
      .click();
    await page
      .getByRole("button", { name: "1. 거래 작성", exact: true })
      .click();
    await page
      .getByRole("button", { name: "2. 전자서명", exact: true })
      .click();
    await page
      .getByRole("button", { name: "잘못된 서명 실험", exact: true })
      .click();
    await expect(page.locator(".validation .result.bad")).toBeVisible();
    await checkReadability(page);
    await page
      .getByRole("button", { name: "3. 검증·전파", exact: true })
      .click();
    await expect(page.locator(".validation .result.good")).toBeVisible();
    await expect(page.locator(".trace-card")).toHaveCount(3);
    await capture("wallet");
    await navigate(page, "검증·Mempool");
    await page.locator(".tx-row > summary").first().click();
    await capture("mempool");
    await navigate(page, "노드·블록체인");
    await page
      .getByRole("button", { name: "메시지 1개 전달", exact: true })
      .click();
    await expect(page.getByTestId("selected-message-route")).toBeVisible();
    await expect(page.getByTestId("recent-message-route")).toBeVisible();
    await capture("network");
    await navigate(page, "채굴");
    await page
      .getByRole("button", { name: "한 번 계산하기", exact: true })
      .click();
    await expect(page.getByTestId("mining-sample").first()).toBeVisible();
    await capture("mining");
    if (
      await page
        .getByRole("button", { name: "계속 채굴", exact: true })
        .isVisible()
    )
      await page
        .getByRole("button", { name: "계속 채굴", exact: true })
        .click();
    await expect(
      page.getByText("✓ 채굴 성공 ·", { exact: false }),
    ).toBeVisible();
    await navigate(page, "변조 실험");
    await page
      .getByRole("button", { name: "복사본 변조·검증", exact: true })
      .click();
    await expect(page.locator(".result.good")).toBeVisible();
    await expect(page.locator(".result.bad")).toBeVisible();
    await capture("tamper");
    // 한 테마마다 모바일의 선택기와 도식도 실제 레이아웃으로 확인한다.
    await page.setViewportSize({ width: 390, height: 900 });
    await expect(page.getByLabel("화면 테마", { exact: true })).toBeVisible();
    await navigate(page, "노드·블록체인");
    await checkReadability(page);
  });
}
