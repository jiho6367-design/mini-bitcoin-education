import { expect, test, type Page } from "@playwright/test";
import manifest from "../package.json" with { type: "json" };
import lockfile from "../package-lock.json" with { type: "json" };
import { calculateNonce, validPow } from "../src/core/block";
import type { Block } from "../src/core/types";
import type { MiningCommand, MiningUpdate } from "../src/mining/protocol";

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

// 실제 계산의 확률과 무관하게 App이 같은 Worker·작업을 유지하는지 검사한다.
// 실제 Worker 계산과 테마 전환은 아래 별도 테스트에서 고정 헤더로 검사한다.
async function installTrackedWorker(page: Page) {
  await page.addInitScript(() => {
    const audit = {
      created: 0,
      terminated: 0,
      jobIds: [] as string[],
      commands: [] as string[],
    };
    Object.assign(window, { __themeWorkerAudit: audit });
    class TrackedWorker {
      onmessage: ((event: { data: unknown }) => void) | null = null;
      onerror = null;
      timer: ReturnType<typeof setInterval> | undefined;
      jobId = "";
      candidate: Record<string, unknown> = {};
      mode = "fast";
      attempts = 0;
      constructor() {
        audit.created++;
      }
      clear() {
        clearInterval(this.timer);
        this.timer = undefined;
      }
      emit(type: string) {
        this.onmessage?.({
          data: {
            type,
            jobId: this.jobId,
            attempts: this.attempts,
            mode: this.mode,
            elapsedMs: this.attempts,
            observationMs: this.attempts * 250,
            samples: [],
            block: {
              ...this.candidate,
              nonce: this.attempts - 1,
              hash: "f".repeat(64),
            },
          },
        });
      }
      progress() {
        this.attempts++;
        this.emit("progress");
      }
      run() {
        this.clear();
        this.progress();
        this.timer = setInterval(() => this.progress(), 60);
      }
      postMessage(command: {
        type: string;
        jobId: string;
        candidate?: Record<string, unknown>;
        mode?: string;
        step?: boolean;
      }) {
        audit.commands.push(command.type);
        if (command.type === "start") {
          this.jobId = command.jobId;
          audit.jobIds.push(this.jobId);
          this.candidate = command.candidate ?? {};
          this.mode = command.mode ?? "fast";
          if (command.step) {
            this.attempts++;
            this.emit("paused");
          } else this.run();
        } else if (command.jobId === this.jobId) {
          if (command.type === "pause") {
            this.clear();
            this.emit("paused");
          }
          if (command.type === "resume") this.run();
          if (command.type === "step") {
            this.attempts++;
            this.emit("paused");
          }
          if (command.type === "stop") {
            this.clear();
            this.emit("stopped");
          }
        }
      }
      terminate() {
        this.clear();
        audit.terminated++;
      }
    }
    Object.defineProperty(window, "Worker", { value: TrackedWorker });
  });
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

test("실행 중·일시정지 중 테마를 바꿔도 앱의 Worker와 작업 ID를 유지한다", async ({
  page,
}) => {
  await installTrackedWorker(page);
  await start(page, "4");
  await submit(page);
  await navigate(page, "채굴");
  await page
    .getByRole("button", { name: "천천히 학습 실행", exact: true })
    .click();
  await expect(page.getByText("● 계산 중", { exact: true })).toBeVisible();
  const readAudit = () =>
    page.evaluate(
      () =>
        (
          window as unknown as {
            __themeWorkerAudit: {
              created: number;
              terminated: number;
              jobIds: string[];
              commands: string[];
            };
          }
        ).__themeWorkerAudit,
    );
  const initial = await readAudit();
  const attempts = Number(
    (await page.getByTestId("attempts").textContent())!.replaceAll(",", ""),
  );
  await selectTheme(page, "dark");
  await expect(page.getByText("● 계산 중", { exact: true })).toBeVisible();
  await expect
    .poll(async () =>
      Number(
        (await page.getByTestId("attempts").textContent())!.replaceAll(",", ""),
      ),
    )
    .toBeGreaterThan(attempts);
  expect(await readAudit()).toEqual(initial);
  await page.getByRole("button", { name: "일시정지", exact: true }).click();
  await expect(page.getByText("● 일시정지", { exact: true })).toBeVisible();
  const pausedAttempts = await page.getByTestId("attempts").textContent();
  const pausedNonce = await page.getByTestId("nonce").textContent();
  const paused = await readAudit();
  await selectTheme(page, "light");
  await selectTheme(page, "system");
  await page.waitForTimeout(150);
  await expect(page.getByTestId("attempts")).toHaveText(pausedAttempts!);
  await expect(page.getByTestId("nonce")).toHaveText(pausedNonce!);
  await expect(page.getByText("● 일시정지", { exact: true })).toBeVisible();
  expect(await readAudit()).toEqual(paused);
  expect(paused.created).toBe(1);
  expect(paused.terminated).toBe(0);
  expect(paused.jobIds).toHaveLength(1);
  await page.getByRole("button", { name: "계속 채굴", exact: true }).click();
  await expect(page.getByText("● 계산 중", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "채굴 중지", exact: true }).click();
  await expect(page.getByText("● 중지됨", { exact: true })).toBeVisible();
  expect((await readAudit()).terminated).toBe(1);
});

// 처음 32개는 실패하는 고정 후보를 선택하여 테마 조작 중 우연한 채굴 성공을 제거한다.
const fixedCandidate: Block = {
  sessionId: "1".repeat(64),
  height: 1,
  previousHash: "0".repeat(64),
  transactionsHash: "2".repeat(64),
  timestamp: 1000,
  difficulty: 4,
  nonce: 0,
  hash: "",
  transactions: [],
};
while (
  Array.from({ length: 32 }, (_, nonce) => nonce).some((nonce) =>
    validPow(
      calculateNonce(fixedCandidate, nonce).hash,
      fixedCandidate.difficulty,
    ),
  )
)
  fixedCandidate.timestamp++;

test("실제 Worker의 Nonce 계산은 테마 전환 뒤에도 계속되고 일시정지는 유지된다", async ({
  page,
}) => {
  await page.goto("/");
  await page.evaluate(async (block) => {
    const worker = new Worker("/src/mining/miner.worker.ts", {
      type: "module",
    });
    const updates: MiningUpdate[] = [];
    Object.assign(window, { __themeActualWorker: { worker, updates } });
    await new Promise<void>((resolve) => {
      worker.onmessage = (event: MessageEvent<MiningUpdate>) => {
        updates.push(event.data);
        resolve();
      };
      worker.postMessage({
        type: "start",
        jobId: "theme-actual",
        candidate: block,
        step: false,
        mode: "learning",
      } satisfies MiningCommand);
    });
  }, fixedCandidate);
  const lastUpdate = () =>
    page.evaluate(
      () =>
        (
          window as unknown as {
            __themeActualWorker: { updates: MiningUpdate[] };
          }
        ).__themeActualWorker.updates.at(-1)!,
    );
  const first = await lastUpdate();
  await selectTheme(page, "dark");
  await expect
    .poll(async () => (await lastUpdate()).attempts)
    .toBeGreaterThan(first.attempts);
  await page.evaluate(() =>
    (
      window as unknown as { __themeActualWorker: { worker: Worker } }
    ).__themeActualWorker.worker.postMessage({
      type: "pause",
      jobId: "theme-actual",
    } satisfies MiningCommand),
  );
  await expect.poll(async () => (await lastUpdate()).type).toBe("paused");
  const paused = await lastUpdate();
  await selectTheme(page, "light");
  await page.waitForTimeout(300);
  expect(await lastUpdate()).toEqual(paused);
  expect(paused.jobId).toBe("theme-actual");
  expect(paused.mode).toBe("learning");
  expect(paused.block?.hash).toBe(
    calculateNonce(fixedCandidate, paused.block!.nonce).hash,
  );
  await page.evaluate(() =>
    (
      window as unknown as { __themeActualWorker: { worker: Worker } }
    ).__themeActualWorker.worker.terminate(),
  );
});

test("버전은 package.json·잠금 파일·화면이 일치하고 하단 문구가 정확하다", async ({
  page,
}) => {
  expect(lockfile.version).toBe(manifest.version);
  expect(lockfile.packages[""].version).toBe(manifest.version);
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
