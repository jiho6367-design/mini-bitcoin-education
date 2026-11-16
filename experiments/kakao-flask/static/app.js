import {
  createSerialQueue,
  draftFingerprint,
  jobIsActive,
  acceptsRevision,
  strictInteger,
  escapeHtml as esc,
  latestActualSample,
  mergeMiningSnapshot,
  confirmedBlockIndex,
} from "./ui-state.js";

const $ = (selector) => document.querySelector(selector);
const queue = createSerialQueue();
const pages = {
  home: ["시작하기", "블록체인 실습", "지갑 → 거래 → 채굴 → 검증"],
  wallet: ["지갑", "지갑", "지갑을 만들고 잔액을 확인하세요"],
  transaction: ["거래·서명", "거래·서명", "작성 → 서명 → 검증·제출"],
  mempool: ["거래 대기열", "거래 대기열", "블록에 포함되기 전의 거래"],
  mining: ["채굴", "채굴", "Nonce를 바꿔 목표 해시를 찾으세요"],
  chain: ["블록·변조", "블록·변조", "기록을 바꾸고 검증 결과를 비교하세요"],
  network: ["노드·동기화", "노드·동기화", "연결 상태와 노드별 장부 비교"],
  events: ["활동 기록", "활동 기록", "거래·채굴·검증·동기화 기록"],
};
const model = {
  page: "home",
  node: "A",
  state: null,
  practice: null,
  wallets: [],
  mempool: [],
  chain: null,
  events: [],
  epoch: 0,
  pending: 0,
  controlPending: false,
  txGeneration: 0,
  tx: null,
  txStage: "empty",
  txFeedback: "",
  formTouched: false,
  walletSignature: "",
  mining: null,
  miningView: "single",
  comparison: null,
  comparisonToken: 0,
  comparisonTimer: null,
  comparisonLooping: false,
  mineMode: "slow",
  selectedSample: null,
  loopToken: 0,
  loopTimer: null,
  looping: false,
  tamper: null,
  tamperTouched: false,
  tamperToken: 0,
  tamperTimer: null,
  tamperLooping: false,
  confirmation: null,
  recoveryNotices: new Set(),
  saveProblem: "",
  serverReady: false,
};
const preparingMessage = "서버 준비 중 준비되면 자동으로 연결됩니다";
let recoveryTimer = null;
let recoveryRunning = false;
let recoveryDelay = 2000;
let recoveryUncertain = false;
let pageActive = true;

function beginConnectionRecovery(uncertain = false) {
  model.serverReady = false;
  recoveryUncertain ||= uncertain;
  stopMineLoop();
  stopComparisonLoop();
  stopTamperLoop();
  notice(navigator.onLine === false
    ? "인터넷 연결을 기다리는 중 연결되면 자동으로 다시 연결됩니다"
    : preparingMessage);
  renderControls();
  renderSaveStatus();
  if (navigator.onLine === false) {
    clearTimeout(recoveryTimer);
    recoveryTimer = null;
    return;
  }
  if (!pageActive || navigator.onLine === false || recoveryTimer !== null || recoveryRunning) return;
  recoveryTimer = setTimeout(async () => {
    recoveryTimer = null;
    recoveryRunning = true;
    let retry = false;
    try {
      // Read the actual persisted state, never repeat the action whose response was lost.
      await refreshAll();
      if (!model.serverReady) return;
      recoveryDelay = 2000;
      notice(recoveryUncertain
        ? "서버 연결이 복구되었습니다 작업이 처리됐을 수 있으므로 현재 상태를 확인하세요"
        : "서버 연결이 복구되었습니다", "success");
      recoveryUncertain = false;
    } catch (error) {
      retry = error.code === "SERVER_PREPARING" || error instanceof TypeError;
      if (!retry) notice(error.message, "danger");
    } finally {
      recoveryRunning = false;
      renderControls();
      renderSaveStatus();
      if (retry && !model.serverReady && pageActive) {
        recoveryDelay = Math.max(2000, Math.min(recoveryDelay * 2, 10000));
        beginConnectionRecovery();
      }
    }
  }, recoveryDelay);
}

async function readBasicResponse(path, options) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 12000);
  try {
    const response = await fetch(path, { ...options, signal: controller.signal });
    const data = await response.json().catch(() => null);
    const transient = [408, 502, 503, 504].includes(response.status)
      || (response.ok && data === null)
      || (response.status >= 500 && !data?.code);
    if (transient) {
      const uncertain = options.method !== "GET" && !data?.code;
      beginConnectionRecovery(uncertain);
      throw new ApiError(
        data?.reason || `${preparingMessage}${uncertain ? " 작업이 처리됐을 수 있으므로 현재 상태를 확인하세요" : ""}`,
        "SERVER_PREPARING", response.status,
      );
    }
    if (data === null) throw new ApiError("서버 응답을 읽지 못했습니다 접속 주소를 확인하세요", "BAD_RESPONSE", response.status);
    return { response, data };
  } catch (error) {
    if (error instanceof ApiError) throw error;
    const uncertain = options.method !== "GET";
    beginConnectionRecovery(uncertain);
    throw new ApiError(`${preparingMessage}${uncertain ? " 작업이 처리됐을 수 있으므로 현재 상태를 확인하세요" : ""}`, "SERVER_PREPARING", 0);
  } finally {
    clearTimeout(timer);
  }
}
const number = (n) =>
  Number.isFinite(n) ? n.toLocaleString("ko-KR") : String(n ?? "—");
const short = (s, n = 12) => (s ? String(s).slice(0, n) + "…" : "—");
const uiMessage = (value) =>
  String(value ?? "").replace(/([가-힣])\.(?=\s|$)/gu, "$1");
const nameOf = (address) =>
  address === "SYSTEM" || !address
    ? "시스템"
    : (model.wallets.find((w) => w.address === address)?.name ??
      short(address));
const currentNode = () => model.state?.nodes?.find((n) => n.id === model.node);
const busy = () => model.pending > 0;
const activeTamper = () =>
  Boolean(model.tamper && model.tamper.status === "running");
const activeComparison = () => ["running", "paused"].includes(model.comparison?.status);
const activeWork = () => jobIsActive(model.mining) || activeComparison() || activeTamper();
const sleepTimer = (callback, ms) => window.setTimeout(callback, ms);
const txInput = () => ({
  node: model.node,
  from: $("#tx-from").value,
  to: $("#tx-to").value,
  amount: $("#tx-amount").value,
});

class ApiError extends Error {
  constructor(message, code, status) {
    super(message);
    this.code = code;
    this.status = status;
  }
}
class Superseded extends Error {}

function notice(text, kind = "info") {
  const el = $("#global-message");
  el.textContent = uiMessage(text);
  el.className = `notice ${kind}`;
  el.hidden = !text;
}
function stopMineLoop() {
  model.loopToken++;
  clearTimeout(model.loopTimer);
  model.loopTimer = null;
  model.looping = false;
}
function stopComparisonLoop() {
  model.comparisonToken++;
  clearTimeout(model.comparisonTimer);
  model.comparisonTimer = null;
  model.comparisonLooping = false;
}
function stopTamperLoop() {
  model.tamperToken++;
  clearTimeout(model.tamperTimer);
  model.tamperTimer = null;
  model.tamperLooping = false;
}
function invalidateDraft(message = "") {
  model.txGeneration++;
  model.tx = null;
  model.txStage = "empty";
  model.txFeedback = message;
  $("#tx-tamper").checked = false;
  renderTransaction();
  renderControls();
  renderGuide();
}
function clearTransient() {
  model.epoch++;
  stopMineLoop();
  stopComparisonLoop();
  stopTamperLoop();
  model.mining = null;
  model.comparison = null;
  model.miningView = "single";
  model.tamper = null;
  model.selectedSample = null;
  model.tx = null;
  model.txStage = "empty";
  model.txFeedback = "";
  model.txGeneration++;
  model.walletSignature = "";
  model.formTouched = false;
  model.tamperTouched = false;
  $("#wallet-feedback").textContent = "";
  $("#wallet-name").value = "";
  $("#tx-amount").value = "20";
  $("#tx-tamper").checked = false;
  $("#tamper-result").textContent = "";
  $("#tamper-value").value = "1000";
  $("#tamper-mode").value = "simple";
  $("#wallet-public-key").textContent = "";
  $("#wallet-private-key").textContent = "";
  if ($("#keys-dialog").open) $("#keys-dialog").close();
}
function rememberMeta(data, response, allowPracticeChange = false) {
  const p = data && !Array.isArray(data) ? data.practice : null;
  const headerRevision = Number(response.headers.get("X-Practice-Revision"));
  const revision =
    data?.revision ??
    p?.revision ??
    (response.headers.has("X-Practice-Revision") ? headerRevision : null);
  const next = p
    ? { ...p, revision: revision ?? p.revision }
    : model.practice
      ? { ...model.practice, revision: revision ?? model.practice.revision }
      : null;
  if (
    p &&
    model.practice?.id &&
    p.id !== model.practice.id &&
    !allowPracticeChange
  )
    throw new ApiError(
      "다른 탭에서 실습이 바뀌어 다시 불러옵니다",
      "PRACTICE_CHANGED",
      409,
    );
  if (next && !acceptsRevision(model.practice, next))
    throw new Superseded("오래된 응답");
  if (next) model.practice = next;
  model.saveProblem = "";
  renderSaveStatus();
}
async function api(path, method = "GET", payload, options = {}) {
  if (method !== "GET" && !model.serverReady)
    throw new ApiError(preparingMessage, "SERVER_PREPARING", 0);
  const epoch = model.epoch;
  const body =
    method === "GET"
      ? undefined
      : {
          ...(payload ?? {}),
          ...(model.practice?.id
            ? {
                expected_practice_id: model.practice.id,
                expected_revision: model.practice.revision,
              }
            : {}),
        };
  const { response, data } = await readBasicResponse(path, {
    method,
    credentials: "same-origin",
    cache: "no-store",
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  if (epoch !== model.epoch) throw new Superseded("다른 실습의 응답");
  if (!response.ok)
    throw new ApiError(
      data.reason || data.error || "요청 처리 실패",
      data.code,
      response.status,
    );
  rememberMeta(data, response, options.allowPracticeChange);
  if (data?.ok === false)
    throw new ApiError(
      data.reason || data.error || "요청 거부",
      data.code,
      409,
    );
  return data;
}
function execute(label, action, { queued = false, control = false } = {}) {
  if ((!queued && busy()) || (control && model.controlPending))
    return Promise.resolve(false);
  if (control) model.controlPending = true;
  model.pending++;
  renderControls();
  renderSaveStatus(label);
  return queue.run(async () => {
    try {
      await action();
      return true;
    } catch (error) {
      if (!(error instanceof Superseded)) {
        stopMineLoop();
        stopComparisonLoop();
        stopTamperLoop();
        if (error.status >= 500 || error instanceof TypeError)
          model.saveProblem = "저장·연결 상태를 확인해 주세요";
        if (error.status === 409 || error.code === "PRACTICE_CHANGED") {
          invalidateDraft();
          try {
            await refreshAll();
          } catch {
            /* Preserve the original actionable error. */
          }
        }
        if (error.code !== "SERVER_PREPARING") notice(error.message, "danger");
        $("#save-dot").className = "save-dot failed";
      }
      return false;
    } finally {
      model.pending--;
      if (control) model.controlPending = false;
      renderControls();
      renderSaveStatus();
      renderGuide();
    }
  });
}

async function refreshAll(retry = 0) {
  const previousPractice = model.practice?.id;
  const state = await api("/api/state", "GET", undefined, {
    allowPracticeChange: true,
  });
  if (previousPractice && previousPractice !== model.practice?.id) {
    clearTransient();
    notice("다른 실습을 불러와 작성 중인 거래를 초기화했습니다");
  }
  const epoch = model.epoch;
  const selectedNode =
    state.nodes.find((n) => n.id === model.node) ?? state.nodes[0];
  const node = selectedNode.id;
  const wallets = await api(`/api/wallets?node=${encodeURIComponent(node)}`);
  const mempool = await api(`/api/nodes/${encodeURIComponent(node)}/mempool`);
  const chain = await api(`/api/nodes/${encodeURIComponent(node)}/chain`);
  const events = await api("/api/events?since=0");
  if (epoch !== model.epoch || node !== model.node)
    throw new Superseded("관찰 노드가 바뀐 응답");
  if (
    state.revision !== undefined &&
    state.revision !== model.practice?.revision
  ) {
    if (retry < 1) return refreshAll(retry + 1);
    throw new ApiError(
      "다른 탭에서 변경 중 · 잠시 후 새로고침하세요",
      "STATE_CHANGED",
      409,
    );
  }
  model.state = state;
  model.wallets = wallets;
  model.mempool = mempool;
  model.chain = chain;
  model.events = events;
  const comparison = state.mining_comparison ?? null;
  if (comparison?.comparison_id !== model.comparison?.comparison_id ||
      !["running", "paused"].includes(comparison?.status)) stopComparisonLoop();
  model.comparison = comparison;
  if (activeComparison()) model.miningView = "compare";
  if (
    model.tx?.signed &&
    ["submitted", "confirmed", "unconfirmed"].includes(model.txStage)
  ) {
    const txid = model.tx.signed.txid;
    const includedAt = confirmedBlockIndex(chain, txid);
    if (includedAt !== null) {
      model.txStage = "confirmed";
      model.txFeedback = `블록 #${includedAt} · Node ${node} 반영 완료`;
    } else if (chain.valid && mempool.some((tx) => tx.txid === txid)) {
      model.txStage = "submitted";
      model.txFeedback = "대기열에 등록됨 · 채굴 후 잔액 반영";
    } else if (
      model.txStage === "confirmed" ||
      model.txStage === "unconfirmed" ||
      !chain.valid
    ) {
      model.txStage = "unconfirmed";
      model.txFeedback = chain.valid
        ? "현재 체인에 없는 거래 · 대기열과 노드를 확인하세요"
        : "체인 검증 실패 · 동기화 후 확정 상태를 확인하세요";
    }
  }
  const candidate = selectedNode.candidate;
  if (candidate) {
    if (
      candidate.job_id !== model.mining?.job_id ||
      model.mining?.node !== node
    ) {
      stopMineLoop();
      model.selectedSample = null;
      model.mineMode = "slow";
    }
    model.mining = mergeMiningSnapshot(model.mining, candidate, node);
  } else if (model.mining?.node === node && jobIsActive(model.mining)) {
    stopMineLoop();
    model.mining = null;
  }
  model.tamper = state.tamper_job ?? (activeTamper() ? null : model.tamper);
  if (!activeTamper()) stopTamperLoop();
  const activeElsewhere = state.nodes.find(
    (n) => n.id !== node && jobIsActive(n.candidate),
  );
  if (activeElsewhere && !jobIsActive(model.mining) && !activeComparison()) {
    notice(`Node ${activeElsewhere.id}에 진행 중인 채굴이 있습니다`);
  }
  const recovery =
    state.practice?.recovery_notice ||
    (state.practice?.job_cancelled_on_restore
      ? "실습 복원 완료 · 진행 중이던 계산은 중지됨"
      : "");
  if (
    recovery &&
    !model.recoveryNotices.has(`${model.practice?.id}:${recovery}`)
  ) {
    notice(recovery);
    model.recoveryNotices.add(`${model.practice?.id}:${recovery}`);
  }
  model.serverReady = navigator.onLine !== false;
  renderAll();
}
function renderSaveStatus(label = "") {
  const p = model.practice;
  $("#practice-label").textContent = p
    ? `실습 · ${String(p.id).slice(0, 8)}`
    : "실습 불러오는 중";
  $("#save-dot").className =
    `save-dot${model.saveProblem ? " failed" : busy() ? " pending" : ""}`;
  if (!model.serverReady) {
    $("#save-status").textContent = navigator.onLine === false ? "인터넷 연결 대기" : "서버 준비 중";
    return;
  }
  if (model.saveProblem) {
    $("#save-status").textContent = model.saveProblem;
    return;
  }
  if (busy())
    $("#save-status").textContent = label
      ? `${label} 처리 중…`
      : "변경 사항 확인 중…";
  else if (p?.saved_at) {
    const date = new Date(p.saved_at);
    $("#save-status").textContent = Number.isNaN(date.getTime())
      ? "자동 저장됨"
      : `자동 저장됨 · ${date.toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" })}`;
  } else
    $("#save-status").textContent = p
      ? "자동 저장"
      : "서버 연결을 확인해 주세요";
}
function setPage(page, updateHash = true) {
  if (!pages[page]) page = "home";
  if (model.page === "home" && page !== "home") $("#demo-video").pause();
  model.page = page;
  document.querySelectorAll(".page").forEach((el) => {
    el.hidden = el.id !== `page-${page}`;
  });
  document.querySelectorAll("#primary-nav [data-page]").forEach((el) => {
    if (el.dataset.page === page) el.setAttribute("aria-current", "page");
    else el.removeAttribute("aria-current");
  });
  const [title, heading, description] = pages[page];
  $("#breadcrumb").textContent = title;
  $("#page-title").textContent = heading;
  $("#page-description").textContent = description;
  document.title = `${title} · 블록체인 실습실`;
  const guide = $("#learning-guide");
  const container = $(`#page-${page}`);
  container.prepend(guide);
  if (updateHash) history.replaceState(null, "", `#${page}`);
  renderGuide();
  window.scrollTo({ top: 0, behavior: "instant" });
}
function renderGuide() {
  const users = model.wallets.filter((w) => !w.is_node_wallet);
  const hasPending = model.mempool.length > 0;
  const valid = model.chain?.valid !== false;
  const guides = {
    home: [
      "실습 시작",
      users.length < 2
        ? "송금할 지갑 두 개를 만드세요"
        : "받는 지갑과 금액을 정해 거래를 작성하세요",
      "거래는 채굴 후 장부에 반영됩니다",
      users.length < 2 ? "wallet" : "transaction",
      users.length < 2 ? "지갑 만들기 →" : "거래 작성 →",
    ],
    wallet: [
      "지갑 준비",
      users.length < 2
        ? "이름을 입력해 지갑을 만드세요"
        : hasPending
          ? "대기 거래를 채굴하세요"
          : "보낼 지갑과 받을 지갑을 확인하세요",
      "새 지갑에 지급되는 50코인은 채굴 후 확정됩니다",
      hasPending ? "mempool" : users.length < 2 ? "wallet" : "transaction",
      hasPending
        ? "대기열 →"
        : users.length < 2
          ? "이름 입력 →"
          : "거래 작성 →",
    ],
    transaction: [
      "작성 → 서명 → 제출",
      model.txStage === "signed"
        ? "검증·제출을 누르세요"
        : model.txStage === "confirmed"
          ? "다른 노드에도 거래가 반영됐는지 확인하세요"
          : model.txStage === "submitted"
            ? "대기열에서 거래를 확인하세요"
            : model.txStage === "prepared"
              ? "입력 내용을 확인하고 서명하세요"
              : "보내는 지갑·받는 지갑·금액을 입력하세요",
      "서명 후 내용이 바뀌면 검증에 실패합니다",
      model.txStage === "confirmed"
        ? "network"
        : model.txStage === "submitted"
          ? "mempool"
          : "transaction",
      model.txStage === "confirmed"
        ? "노드 비교 →"
        : model.txStage === "submitted"
          ? "대기열 →"
          : "거래 입력 →",
    ],
    mempool: [
      "채굴 대기",
      hasPending
        ? "대기 거래를 확인하고 채굴하세요"
        : "지갑을 만들거나 거래를 제출하세요",
      "블록에 포함되면 확정 잔액에 반영됩니다",
      hasPending ? "mining" : users.length < 2 ? "wallet" : "transaction",
      hasPending ? "채굴 →" : "거래 준비 →",
    ],
    mining: [
      "Nonce와 해시",
      jobIsActive(model.mining)
        ? model.mining.status === "paused"
          ? "표본을 비교하거나 계산을 이어가세요"
          : "일시정지하면 표본을 비교할 수 있습니다"
        : model.mining?.status === "found"
          ? "다른 노드의 블록 반영 상태를 확인하세요"
          : "한 번 계산하거나 느린 실행을 시작하세요",
      "해시 앞의 0 개수가 목표 난이도입니다",
      model.mining?.status === "found" ? "network" : "mining",
      model.mining?.status === "found" ? "노드 비교 →" : "계산하기 →",
    ],
    chain: [
      "변조와 복구",
      !valid
        ? "실패한 검사를 확인하고 동기화하세요"
        : (model.chain?.height ?? 0) < 1
          ? "먼저 거래가 담긴 블록을 채굴하세요"
          : "블록·거래·새 값을 선택하세요",
      "재채굴해도 변경된 송금의 원래 서명은 무효입니다",
      !valid ? "network" : (model.chain?.height ?? 0) < 1 ? "mining" : "chain",
      !valid
        ? "복구하기 →"
        : (model.chain?.height ?? 0) < 1
          ? "채굴 →"
          : "변조하기 →",
    ],
    network: [
      "분리와 동기화",
      "노드를 분리해 채굴한 뒤 다시 연결하세요",
      "동기화는 유효한 체인의 누적 작업량을 비교합니다",
      "network",
      "노드 비교 →",
    ],
    events: [
      "기록 조회",
      "유형별 기록 확인",
      "최근 기록부터 표시",
      "events",
      "기록 보기 →",
    ],
  };
  if (model.miningView === "compare") guides.mining = [
    "세 노드 채굴 비교",
    model.comparison?.status === "found"
      ? "승자와 세 노드의 블록 반영 결과를 확인하세요"
      : activeComparison()
        ? model.comparisonLooping ? "Nonce·해시·시도 횟수가 함께 바뀌는지 보세요" : "한 번씩 계산하거나 계속 비교를 누르세요"
        : "세 노드 비교 시작을 누르세요",
    "같은 난이도라도 먼저 찾는 노드는 달라질 수 있습니다",
    "mining", "비교 화면 보기 →",
  ];
  const guide = guides[model.page];
  $("#learning-guide").hidden = model.page === "events";
  $("#guide-title").textContent = guide[0];
  $("#guide-action").textContent = guide[1];
  $("#guide-observe").textContent = guide[2];
  $("#guide-next").textContent = guide[4];
  $("#guide-next").dataset.destination = guide[3];
}

function fillWalletSelect(id, values, preferred) {
  const el = $(id);
  const previous = el.value;
  el.innerHTML = values.length
    ? values
        .map((w) => `<option value="${esc(w.address)}">${esc(w.name)}</option>`)
        .join("")
    : '<option value="">지갑을 먼저 만들어 주세요</option>';
  el.value = values.some((w) => w.address === previous)
    ? previous
    : (preferred ?? values[0]?.address ?? "");
}
function renderWallets() {
  $("#wallet-node").textContent = `· Node ${model.node}`;
  $("#wallet-count").textContent = `${model.wallets.length}개`;
  $("#wallet-list").innerHTML = model.wallets
    .map(
      (w) =>
        `<tr><td><div class="wallet-name"><strong>${esc(w.name)}</strong>${w.is_node_wallet ? '<span class="tiny-tag">노드 지갑</span>' : ""}</div></td><td class="mono" title="${esc(w.address)}">${esc(short(w.address, 15))}</td><td class="numeric"><strong class="${w.balance_valid === false ? "bad" : ""}">${w.balance_valid === false || w.balance == null ? "검증 필요" : number(w.balance)}</strong></td><td>${w.pending_in ? `<span class="ok">+${number(w.pending_in)}</span> ` : ""}${w.pending_out ? `<span class="bad">−${number(w.pending_out)}</span>` : ""}${!w.pending_in && !w.pending_out ? '<span class="subtle">—</span>' : ""}</td><td><button class="secondary" data-keys="${esc(w.address)}">키 보기</button> <button class="secondary" data-faucet="${esc(w.address)}">+50 지급 요청</button></td></tr>`,
    )
    .join("");
  const signature = model.wallets
    .map((w) => `${w.address}:${w.name}`)
    .join("|");
  if (signature !== model.walletSignature) {
    const users = model.wallets.filter((w) => !w.is_node_wallet);
    fillWalletSelect("#tx-from", model.wallets, users[0]?.address);
    fillWalletSelect(
      "#tx-to",
      model.wallets,
      users[1]?.address ?? users[0]?.address,
    );
    fillWalletSelect("#mining-wallet", model.wallets, currentNode()?.wallet);
    if (!model.formTouched && !model.tx) {
      if (users[0]) $("#tx-from").value = users[0].address;
      if (users[1]) $("#tx-to").value = users[1].address;
    }
    model.walletSignature = signature;
  }
}
function renderTransaction() {
  $("#transaction-node").textContent = `Node ${model.node}에 제출`;
  const wallet = model.wallets.find((w) => w.address === $("#tx-from").value);
  $("#tx-balance-hint").textContent = wallet
    ? wallet.balance_valid === false
      ? "체인 검증 실패 · 동기화가 필요합니다"
      : `${wallet.name} · 확정 ${number(wallet.balance)}코인 · 대기 출금 ${number(wallet.pending_out ?? 0)}코인`
    : "지갑을 먼저 만드세요";
  const labels = {
    empty: "작성 전",
    prepared: "작성 완료",
    signed: "서명 완료",
    submitted: "제출 완료",
    confirmed: "블록 반영 완료",
    unconfirmed: "확정 상태 확인 필요",
  };
  $("#tx-stage").textContent = labels[model.txStage];
  $("#tx-stage").className =
    `status-chip${["submitted", "confirmed"].includes(model.txStage) ? " ok" : model.txStage === "unconfirmed" ? " bad" : ""}`;
  const tx = model.tx;
  $("#tx-payload-details").hidden = !tx;
  $("#tx-signature-details").hidden = !tx?.signed;
  if (!tx) {
    $("#tx-summary").className = "empty-state";
    $("#tx-summary").textContent = "거래 작성 전";
  } else {
    $("#tx-summary").className = "summary-grid";
    $("#tx-summary").innerHTML =
      `<div><span>보내는 지갑</span><strong>${esc(nameOf(tx.draft.sender_address))}</strong></div><div><span>받는 지갑</span><strong>${esc(nameOf(tx.draft.receiver_address))}</strong></div><div><span>송금액</span><strong>${number(tx.draft.amount)} 코인</strong></div><div><span>거래 순번 · Nonce</span><strong>${number(tx.draft.nonce)}</strong></div>`;
    $("#tx-payload").textContent =
      `서명 대상 해시\n${tx.signing_hash}\n\n${JSON.stringify(tx.draft, null, 2)}`;
    if (tx.signed)
      $("#tx-signature").textContent =
        `서명\n${tx.signed.signature}\n\n거래 ID\n${tx.signed.txid}`;
  }
  $("#tx-result").className = model.txFeedback
    ? `result-card${model.txStage === "unconfirmed" || (model.txStage !== "submitted" && model.txFeedback.startsWith("거부")) ? " bad" : ""}`
    : "empty-state";
  $("#tx-result").textContent = uiMessage(
    model.txFeedback ||
      (tx?.signed ? "서명 완료 · 검증·제출을 누르세요" : "서명 전"),
  );
}
function renderMempool() {
  $("#mempool-node").textContent = `· Node ${model.node}`;
  const n = currentNode();
  $("#mempool-metrics").innerHTML =
    `<div><span>대기 거래</span><strong>${model.mempool.length}<em>건</em></strong><small>블록 포함 전</small></div><div><span>체인 높이</span><strong>${number(n?.height ?? 0)}</strong><small>Genesis 제외</small></div>`;
  $("#mempool-list").innerHTML = model.mempool.length
    ? model.mempool
        .map(
          (t) =>
            `<tr><td><span class="tiny-tag">${esc({ faucet: "지급", transfer: "송금", coinbase: "보상" }[t.type] ?? t.type)}</span></td><td>${esc(nameOf(t.sender_address))} → ${esc(nameOf(t.receiver_address))}</td><td class="numeric"><strong>${number(t.amount)}</strong></td><td>${number(t.nonce)}</td><td class="mono" title="${esc(t.txid)}">${esc(short(t.txid, 17))}</td></tr>`,
        )
        .join("")
    : '<tr><td colspan="5"><div class="empty-state">대기 거래 없음</div></td></tr>';
}
function hashHtml(hash, difficulty) {
  const value = String(hash ?? "");
  const zeros = Math.min(Number(difficulty) || 0, value.match(/^0*/)[0].length);
  return `<mark>${esc(value.slice(0, zeros))}</mark>${esc(value.slice(zeros))}`;
}
function renderMining() {
  renderComparison();
  const job = model.mining;
  const sample = latestActualSample(job);
  const labels = {
    running: model.looping ? "계산 중" : "계산 대기",
    paused: "일시정지",
    found: "채굴 성공",
    stopped: "중지됨",
    error: "오류",
  };
  $("#mining-node").textContent =
    `Node ${jobIsActive(job) ? job.node : model.node} · ${currentNode()?.online ? "온라인" : "분리된 로컬 채굴"}`;
  $("#mining-status").textContent = job
    ? (labels[job.status] ?? "준비")
    : "준비";
  $("#mining-nonce").textContent = sample ? number(sample.nonce) : "—";
  $("#mining-hash").innerHTML = sample
    ? hashHtml(sample.hash, job.difficulty ?? model.state?.difficulty)
    : "계산 전";
  $("#mining-attempts").textContent = number(job?.attempts ?? 0);
  $("#mining-elapsed").innerHTML =
    `${((job?.elapsed_ms ?? 0) / 1000).toFixed(3)} <em>초</em>`;
  $("#mining-reward").innerHTML =
    `+${number(model.state?.block_reward ?? 10)} <em>코인</em>`;
  $("#mining-result").textContent = uiMessage(
    job?.resultText ??
      (job?.reason === "restore_cancelled"
        ? "서버 재시작으로 계산 중지 · 체인 유지"
        : job?.status === "paused"
          ? "일시정지 · 한 번 계산하거나 이어가세요"
          : job?.status === "stopped"
            ? "중지됨 · 새 후보로 다시 시작하세요"
            : ""),
  );
  const actualDifficulty = jobIsActive(job)
    ? job.difficulty
    : (currentNode()?.difficulty ?? model.state?.difficulty ?? 2);
  const modeHint = jobIsActive(job)
    ? model.mineMode === "fast"
      ? "연속 실행"
      : "느린 실행 · 약 250ms마다 1회"
    : "";
  $("#mining-mode-hint").textContent =
    `난이도 ${actualDifficulty} · 목표 ${"0".repeat(actualDifficulty)}…${modeHint ? ` · ${modeHint}` : ""}`;
  const samples = job?.samples ?? [];
  $("#mining-samples").className = samples.length
    ? "sample-list"
    : "sample-list empty-state";
  $("#mining-samples").innerHTML = samples.length
    ? samples
        .slice()
        .reverse()
        .map(
          (s) =>
            `<div class="sample-row"><button data-sample="${esc(s.nonce)}" aria-pressed="${model.selectedSample?.nonce === s.nonce}">Nonce ${esc(s.nonce)}</button><code class="sample-hash">${hashHtml(s.hash, job.difficulty ?? model.state?.difficulty)}</code><span class="${s.passed ? "ok" : "subtle"}">${s.passed ? "성공" : "미달"}</span></div>`,
        )
        .join("")
    : "표본 없음";
  $("#selected-sample").hidden = !model.selectedSample;
  if (model.selectedSample)
    $("#selected-sample").innerHTML =
      `<strong>선택한 표본 · Nonce ${esc(model.selectedSample.nonce)}</strong><code>${esc(model.selectedSample.hash)}</code><p>선택한 표본은 다음 계산 중에도 유지됩니다</p>`;
}
function comparisonReadiness() {
  const nodes = model.state?.nodes ?? [];
  if (nodes.length !== 3) return "서버 연결 후 시작할 수 있습니다";
  if (nodes.some((node) => !node.online)) return "노드·동기화에서 세 노드를 모두 온라인으로 바꾸세요";
  if (nodes.some((node) => !node.valid)) return "노드·동기화에서 검증에 실패한 장부를 먼저 복구하세요";
  if (new Set(nodes.map((node) => node.tip_hash)).size !== 1) return "노드·동기화에서 세 노드의 장부를 먼저 동기화하세요";
  return "";
}
function renderComparison() {
  const comparison = model.comparison;
  const comparing = model.miningView === "compare";
  $("#single-mining-panel").hidden = comparing;
  $("#comparison-mining-panel").hidden = !comparing;
  $("#mining-view-single").setAttribute("aria-pressed", String(!comparing));
  $("#mining-view-compare").setAttribute("aria-pressed", String(comparing));
  const found = comparison?.status === "found";
  $("#comparison-status").textContent = found ? `Node ${comparison.winner} 발견`
    : activeComparison() ? model.comparisonLooping ? "세 노드 계산 중" : "계산 대기"
      : comparison?.status === "stopped" ? "중지됨" : "준비";
  const recovery = comparison?.reason === "restore_cancelled";
  $("#comparison-guide").textContent = found
    ? `Node ${comparison.winner}가 먼저 찾았습니다 아래에서 각 노드의 계산과 전달 결과를 비교하세요`
    : activeComparison() ? model.comparisonLooping
      ? "세 노드의 Nonce·해시·시도 횟수를 비교하세요 일시정지하면 천천히 볼 수 있습니다"
      : "한 번씩 계산하거나 계속 비교를 누르세요"
      : comparisonReadiness() || (recovery ? "서버 재시작으로 계산은 중지됐습니다 저장된 장부에서 새 비교를 시작하세요" : "세 노드 비교 시작을 누르세요 먼저 찾은 노드가 나오면 자동으로 멈춥니다");
  const difficulty = model.state?.difficulty ?? 2;
  $("#comparison-target").textContent = `난이도 ${difficulty} · 목표 ${"0".repeat(difficulty)}… · 같은 난이도와 계산 속도로 비교`;
  $("#comparison-nodes").innerHTML = ["A", "B", "C"].map((id) => {
    const node = model.state?.nodes.find((item) => item.id === id);
    const candidate = ["found", "stopped"].includes(comparison?.status)
      ? comparison.candidates?.[id] : node?.candidate;
    const job = candidate?.job_id === comparison?.job_ids?.[id] ? candidate : null;
    const sample = latestActualSample(job);
    const winner = found && comparison.winner === id;
    const label = winner ? "먼저 발견" : found ? "계산 종료" : comparison?.status === "stopped" ? "중지됨" : job
      ? model.comparisonLooping ? "계산 중" : "계산 대기" : "준비";
    return `<article class="comparison-card${winner ? " winner" : ""}" data-comparison-node="${id}"><h3>Node ${id}<span class="tiny-tag">${label}</span></h3><dl><dt>실제 시도 횟수</dt><dd data-comparison-attempts>${number(job?.attempts ?? 0)}회</dd><dt>최근 계산 Nonce</dt><dd>${sample ? number(sample.nonce) : "—"}</dd><dt>해시 계산 시간</dt><dd>${(job?.elapsed_ms ?? 0).toFixed(3)}ms</dd></dl><p class="hash-label">최근 실제 계산한 해시</p><code>${sample ? hashHtml(sample.hash, job.difficulty) : "계산 전"}</code><p class="comparison-chain">${node ? `현재 장부 #${number(node.height)} · ${node.valid ? "검증 통과" : "검증 필요"}` : "장부 불러오는 중"}</p></article>`;
  }).join("");
  $("#comparison-result-panel").hidden = !found;
  if (found) {
    const deliveries = comparison.propagation ?? [];
    $("#comparison-result").textContent = `Node ${comparison.winner}에서 블록 #${comparison.index} 발견${deliveries.length ? ` · ${deliveries.map((result) => `Node ${result.node} ${result.accepted ? "검증·저장 완료" : "전달 확인 필요"}`).join(" · ")}` : " · 각 노드의 현재 장부를 확인하세요"}`;
  }
}
function renderChain() {
  const chain = model.chain;
  $("#chain-status").innerHTML =
    `<span class="status-chip ${chain?.valid ? "ok" : "bad"}">${chain?.valid ? "✓ 체인 유효" : "검증 실패 · 잔액 확인 불가"}</span>`;
  const checks = {
    hash: "해시",
    merkle: "Merkle",
    pow: "PoW",
    link: "연결",
    txs: "거래",
  };
  $("#chain-list").innerHTML = (chain?.blocks ?? [])
    .map(
      (b) =>
        `<article class="block-card${b.valid ? "" : " invalid"}"><h3>${b.index === 0 ? "Genesis" : "블록"} #${b.index}<span class="subtle">${b.transactions.length}개 거래</span></h3><div class="check-badges">${Object.entries(
          checks,
        )
          .map(
            ([key, label]) =>
              `<span class="${b.checks[key] ? "" : "failed"}">${b.checks[key] ? "✓" : "×"} ${label}</span>`,
          )
          .join(
            "",
          )}</div><dl><dt>블록 해시</dt><dd>${esc(b.hash)}</dd><dt>앞 블록 해시</dt><dd>${esc(b.previous_hash)}</dd><dt>Merkle Root</dt><dd>${esc(b.merkle_root)}</dd><dt>Nonce · 난이도</dt><dd>${number(b.nonce)} · ${number(b.difficulty)}</dd></dl>${b.transactions.length ? `<details><summary>거래 ${b.transactions.length}개 보기</summary>${b.transactions.map((t, i) => `<div class="tx-line">#${i} ${esc({ coinbase: "채굴 보상", faucet: "지급", transfer: "송금" }[t.type] ?? t.type)}<br>${esc(nameOf(t.sender_address))} → ${esc(nameOf(t.receiver_address))} · <b>${number(t.amount)}</b></div>`).join("")}</details>` : ""}${b.reasons?.length ? `<p class="reason">${b.reasons.map((reason) => esc(uiMessage(reason))).join("<br>")}</p>` : ""}</article>`,
    )
    .join("");
  renderTamperOptions();
}
function renderTamperOptions() {
  const blocks = (model.chain?.blocks ?? []).filter(
    (b) => b.index > 0 && b.transactions.length,
  );
  const el = $("#tamper-block");
  const previous = el.value;
  el.innerHTML = blocks.length
    ? blocks
        .map(
          (b) =>
            `<option value="${b.index}">블록 #${b.index} · 거래 ${b.transactions.length}개</option>`,
        )
        .join("")
    : '<option value="">블록을 먼저 채굴하세요</option>';
  const preferred =
    blocks
      .slice()
      .reverse()
      .find((b) => b.transactions.some((t) => t.type === "transfer")) ??
    blocks.at(-1);
  el.value =
    model.tamperTouched && blocks.some((b) => String(b.index) === previous)
      ? previous
      : String(preferred?.index ?? "");
  renderTamperTransactions();
}
function renderTamperTransactions() {
  const block = model.chain?.blocks?.find(
    (b) => b.index === Number($("#tamper-block").value),
  );
  const el = $("#tamper-tx");
  const previous = el.value;
  el.innerHTML = block
    ? block.transactions
        .map(
          (t, i) =>
            `<option value="${i}">#${i} ${esc({ coinbase: "채굴 보상", faucet: "지급", transfer: "서명된 송금" }[t.type] ?? t.type)} · ${esc(nameOf(t.receiver_address))} ${t.amount}</option>`,
        )
        .join("")
    : '<option value="">거래 없음</option>';
  const defaultIndex =
    block?.transactions.findIndex((t) => t.type === "transfer") ?? -1;
  el.value =
    model.tamperTouched && block?.transactions[Number(previous)]
      ? previous
      : String(defaultIndex >= 0 ? defaultIndex : 0);
  renderTamperTarget();
}
function renderTamperTarget() {
  const block = model.chain?.blocks?.find(
    (b) => b.index === Number($("#tamper-block").value),
  );
  const tx = block?.transactions[Number($("#tamper-tx").value)];
  $("#tamper-target").textContent = tx
    ? `변경 대상: Node ${model.node} · 블록 #${block.index} · 거래 #${$("#tamper-tx").value} · ${nameOf(tx.sender_address)} → ${nameOf(tx.receiver_address)} · 현재 ${tx.amount}코인`
    : "변조할 블록 없음 · 먼저 채굴하세요";
}
function renderNetwork() {
  $("#network-list").innerHTML = (model.state?.nodes ?? [])
    .map(
      (n) =>
        `<article class="network-card${n.id === model.node ? " observed" : ""}"><h2>${esc(n.name)}${n.id === model.node ? '<span class="tiny-tag">관찰 중</span>' : ""}</h2><span class="connection${n.online ? "" : " offline"}">${n.online ? "네트워크 연결됨" : "네트워크에서 분리됨"}</span><div class="height">${number(n.height)} <small>블록 높이</small></div><dl><dt>마지막 블록 해시</dt><dd class="mono">${esc(n.tip_hash)}</dd><dt>검증 · 대기 거래</dt><dd class="${n.valid ? "ok" : "bad"}">${n.valid ? "✓ 체인 유효" : "× 변조 검출"} · ${n.mempool}건 대기</dd><dt>누적 작업량 · 채굴 상태</dt><dd>${esc(n.cumulative_work ?? "—")} · ${esc({ running: "계산 후보 준비됨", paused: "일시정지", found: "발견 완료", stopped: "중지됨", idle: "준비" }[n.mining_status] ?? "준비")}</dd></dl><div class="actions"><button class="secondary" data-online="${esc(n.id)}" data-next-online="${!n.online}">${n.online ? "오프라인으로" : "온라인으로"}</button><button class="secondary" data-sync="${esc(n.id)}" data-testid="node-sync-${esc(n.id)}" ${n.online ? "" : "disabled"}>동기화</button><button class="text-button" data-observe="${esc(n.id)}">관찰 →</button></div></article>`,
    )
    .join("");
}
function renderEvents() {
  const filter = $("#events-filter").value;
  const selected = model.events.filter(
    (e) =>
      filter === "all" ||
      e.kind === filter ||
      (filter === "block" && e.kind === "mine"),
  );
  $("#events-count").textContent = `${selected.length}개 · 최신순`;
  const kinds = new Set([
    "tx",
    "mine",
    "block",
    "reject",
    "sync",
    "tamper",
    "sys",
  ]);
  $("#events-list").innerHTML =
    selected
      .slice()
      .reverse()
      .map(
        (e) =>
          `<li class="kind-${kinds.has(e.kind) ? e.kind : "sys"}"><time>${esc(e.time)}</time><strong>${e.node === "-" ? "실습실" : `Node ${esc(e.node)}`}</strong><p>${esc(uiMessage(e.msg))}</p></li>`,
      )
      .join("") || "<li><p>기록 없음</p></li>";
}
function renderControls() {
  const ready = Boolean(model.state) && model.serverReady;
  const work = activeWork();
  const locked = !ready || busy() || work;
  const set = (id, disabled) => {
    $(id).disabled = Boolean(disabled);
  };
  [
    "#wallet-create",
    "#difficulty-apply",
    "#difficulty",
    "#mining-wallet",
    "#tx-from",
    "#tx-to",
    "#tx-amount",
    "#tx-tamper",
  ].forEach((id) => set(id, locked));
  set("#wallet-name", locked);
  set("#node-select", !ready || busy() || work);
  set("#refresh", !model.serverReady || busy());
  set("#practice-new", !model.serverReady || busy());
  set("#practice-reset", !ready || busy());
  const validChain = model.chain?.valid !== false;
  set("#tx-prepare", locked || model.wallets.length < 2 || !validChain);
  const fingerprint = draftFingerprint(txInput());
  const same =
    model.tx &&
    model.tx.fingerprint === fingerprint &&
    model.tx.node === model.node;
  set("#tx-sign", locked || !same || model.txStage !== "prepared");
  set("#tx-submit", locked || !same || model.txStage !== "signed");
  const mining = jobIsActive(model.mining);
  const controlsLocked = !ready || model.controlPending || activeTamper() || activeComparison();
  set(
    "#mining-step",
    controlsLocked ||
      busy() ||
      !validChain ||
      (mining && model.mining.status === "running"),
  );
  set("#mining-slow", controlsLocked || busy() || !validChain || mining);
  set("#mining-fast", controlsLocked || busy() || !validChain || mining);
  set("#mining-start", controlsLocked || busy() || mining || !validChain);
  set(
    "#mining-pause",
    controlsLocked ||
      !mining ||
      model.mining.status !== "running" ||
      !model.looping,
  );
  set("#mining-resume", controlsLocked || busy() || !mining || model.looping);
  set("#mining-stop", controlsLocked || !mining);
  const comparison = activeComparison();
  const comparisonLocked = !ready || busy() || model.controlPending || activeTamper();
  const singleWork = mining && !comparison;
  set("#mining-view-single", busy() || comparison || model.comparisonLooping);
  set("#mining-view-compare", busy() || singleWork || activeTamper());
  set("#comparison-start", comparisonLocked || mining || comparison || Boolean(comparisonReadiness()));
  set("#comparison-step", comparisonLocked || singleWork || model.comparisonLooping || (!comparison && Boolean(comparisonReadiness())));
  set("#comparison-pause", !ready || model.controlPending || !comparison || !model.comparisonLooping);
  set("#comparison-resume", comparisonLocked || !comparison || model.comparisonLooping);
  set("#comparison-stop", !ready || model.controlPending || !comparison);
  set("#comparison-batch", comparisonLocked || model.comparisonLooping);
  $("#comparison-start").hidden = comparison;
  $("#comparison-start").textContent = model.comparison ? "다시 비교하기" : "세 노드 비교 시작";
  $("#comparison-step").hidden = model.comparisonLooping || model.comparison?.status === "found";
  $("#comparison-pause").hidden = !model.comparisonLooping;
  $("#comparison-resume").hidden = !comparison || model.comparisonLooping;
  $("#comparison-stop").hidden = !comparison;
  [
    "#tamper-block",
    "#tamper-tx",
    "#tamper-field",
    "#tamper-value",
    "#tamper-mode",
  ].forEach((id) => set(id, !ready || busy() || work));
  set(
    "#tamper-start",
    !ready ||
      busy() ||
      mining || comparison ||
      (activeTamper() && model.tamperLooping) ||
      !$("#tamper-block").value,
  );
  $("#tamper-start").textContent = activeTamper() ? "재채굴 계속" : "변조하기";
  set("#tamper-cancel", !ready || !activeTamper() || model.controlPending);
  set("#chain-sync", locked || !currentNode()?.online);
  set("#invalid-sync", locked || !currentNode()?.online);
  $("#integrity-warning").hidden = !ready || validChain;
  $("#integrity-description").textContent =
    `Node ${model.node} 체인 검증 실패 · 동기화가 필요합니다${currentNode()?.online ? "" : " 먼저 온라인으로하세요"}`;
  document
    .querySelectorAll(
      "[data-faucet],[data-keys],[data-online],[data-sync],[data-observe]",
    )
    .forEach((button) => {
      button.disabled =
        locked ||
        (button.hasAttribute("data-sync") &&
          !model.state.nodes.find((n) => n.id === button.dataset.sync)?.online);
    });
  $("#node-select").value = model.node;
  $("#task-message").hidden = !work;
  if (work)
    $("#task-message").textContent = comparison
      ? `A·B·C 채굴 비교 · ${model.comparisonLooping ? "계산 중" : "계산 대기"} · 설정 변경은 비교 중지 후 가능`
      : activeTamper()
      ? `Node ${model.tamper.node ?? model.node} 재채굴 중 · 취소하면 원본 유지`
      : `Node ${model.mining.node} · ${model.mining.status === "paused" ? "일시정지" : model.looping ? "계산 중" : "후보 준비"} · 설정 변경은 중지 후 가능`;
}
function renderAll() {
  renderWallets();
  renderTransaction();
  renderMempool();
  renderMining();
  renderChain();
  renderNetwork();
  renderEvents();
  renderGuide();
  renderControls();
  renderSaveStatus();
  if (document.activeElement !== $("#difficulty"))
    $("#difficulty").value = model.state?.difficulty ?? 2;
  $("#difficulty").min = model.state?.min_difficulty ?? 1;
  $("#difficulty").max = model.state?.max_difficulty ?? 5;
  $("#version-label").textContent =
    `Flask Learning Lab · ${model.state?.version ? `v${model.state.version}` : "교육용 모델"}`;
}

async function createWallet() {
  const name = $("#wallet-name").value.trim();
  if (!name) {
    $("#wallet-name").focus();
    notice("지갑 이름을 입력하세요", "warning");
    return;
  }
  const result = await api("/api/wallets", "POST", { name, node: model.node });
  $("#wallet-name").value = "";
  $("#wallet-feedback").textContent = result.faucet?.ok
    ? `${name} 지갑 생성 · 지급 상태는 잔액 목록에서 확인`
    : `${name} 지갑 생성 · 지급 결과: ${uiMessage(result.faucet?.reason ?? "대기열을 확인하세요")}`;
  await refreshAll();
  notice(`${name} 지갑 생성 · 50코인 지급 대기`, "success");
}
async function prepareTransaction() {
  const input = txInput();
  const amount = strictInteger(input.amount);
  if (!input.from || !input.to)
    throw new Error("보내는 지갑과 받는 지갑을 선택하세요");
  if (amount === null) throw new Error("금액은 1 이상의 정수로 입력하세요");
  const generation = ++model.txGeneration;
  const fingerprint = draftFingerprint(input);
  const result = await api("/api/tx/prepare", "POST", {
    from: input.from,
    to: input.to,
    amount,
    node: input.node,
  });
  if (
    generation !== model.txGeneration ||
    fingerprint !== draftFingerprint(txInput())
  )
    return;
  model.tx = {
    draft: result.tx,
    signing_hash: result.signing_hash,
    signed: null,
    node: input.node,
    fingerprint,
  };
  model.txStage = "prepared";
  model.txFeedback = "거래 작성 완료 · 내용을 확인하고 서명하세요";
  renderTransaction();
}
async function signTransaction() {
  const tx = model.tx;
  const generation = model.txGeneration;
  if (
    !tx ||
    model.txStage !== "prepared" ||
    tx.fingerprint !== draftFingerprint(txInput())
  )
    throw new Error("현재 입력으로 거래를 다시 작성하세요");
  const result = await api("/api/tx/sign", "POST", { tx: tx.draft });
  if (generation !== model.txGeneration || model.tx !== tx) return;
  tx.signed = result.tx;
  model.txStage = "signed";
  model.txFeedback = "서명 완료 · 검증·제출을 누르세요";
  renderTransaction();
}
async function submitTransaction() {
  const tx = model.tx;
  const generation = model.txGeneration;
  if (
    !tx?.signed ||
    model.txStage !== "signed" ||
    tx.fingerprint !== draftFingerprint(txInput()) ||
    tx.node !== model.node
  )
    throw new Error("입력 변경 · 거래 작성과 서명을 다시 진행하세요");
  const payload = structuredClone(tx.signed);
  const tampered = $("#tx-tamper").checked;
  if (tampered) payload.amount += 1000;
  // A rejected transaction is an expected learning result, not a transport error.
  try {
    await api("/api/tx/submit", "POST", { tx: payload, node: model.node });
  } catch (error) {
    if (
      ["STALE_REVISION", "PRACTICE_CHANGED"].includes(error.code) ||
      error.status >= 500 ||
      !tampered
    )
      throw error;
    model.txFeedback = `거부: ${error.message}`;
    renderTransaction();
    await refreshAll();
    return;
  }
  if (generation !== model.txGeneration || model.tx !== tx) return;
  model.txStage = "submitted";
  model.txFeedback = "대기열 등록 완료 · 채굴 후 잔액 반영";
  await refreshAll();
  renderTransaction();
  notice(
    model.txStage === "confirmed"
      ? "거래 확정 · 잔액과 노드 상태를 확인하세요"
      : "거래 등록 완료 · 채굴 대기",
    "success",
  );
}

function acceptComparison(result) {
  model.comparison = result.comparison;
  if (model.state && result.nodes) {
    model.state = { ...model.state, nodes: result.nodes, mining_comparison: result.comparison };
    const candidate = result.nodes.find((node) => node.id === model.node)?.candidate;
    model.mining = candidate ? { ...candidate, node: model.node } : null;
  }
  if (!activeComparison()) stopComparisonLoop();
  renderComparison();
  renderControls();
  renderGuide();
}
async function ensureComparison() {
  if (activeComparison()) return;
  acceptComparison(await api("/api/mining/comparison/start", "POST", {}));
}
function comparisonContext() {
  return { token: model.comparisonToken, epoch: model.epoch, practice: model.practice?.id };
}
function currentComparisonContext(context) {
  return pageActive && context.token === model.comparisonToken &&
    context.epoch === model.epoch && context.practice === model.practice?.id;
}
async function comparisonStep() {
  const context = comparisonContext();
  await ensureComparison();
  if (!currentComparisonContext(context)) return;
  if (model.comparison.status === "running")
    acceptComparison(await api("/api/mining/comparison/pause", "POST", {
      comparison_id: model.comparison.comparison_id,
    }));
  if (!currentComparisonContext(context)) return;
  acceptComparison(await api("/api/mining/comparison/step", "POST", {
    comparison_id: model.comparison.comparison_id, batch: 1,
  }));
  if (pageActive && context.epoch === model.epoch && !activeComparison()) await refreshAll();
}
async function startComparisonLoop() {
  const context = comparisonContext();
  await ensureComparison();
  if (!currentComparisonContext(context)) return;
  if (model.comparison.status === "paused")
    acceptComparison(await api("/api/mining/comparison/resume", "POST", {
      comparison_id: model.comparison.comparison_id,
    }));
  if (!currentComparisonContext(context)) return;
  stopComparisonLoop();
  model.comparisonLooping = true;
  scheduleComparison(model.comparisonToken, 0);
  renderComparison();
  renderControls();
}
function scheduleComparison(token, delay) {
  model.comparisonTimer = sleepTimer(() => {
    if (!pageActive || token !== model.comparisonToken || !model.comparisonLooping || !activeComparison()) return;
    execute("세 노드 해시 계산", async () => {
      if (!pageActive || token !== model.comparisonToken || !model.comparisonLooping) return;
      const result = await api("/api/mining/comparison/step", "POST", {
        comparison_id: model.comparison.comparison_id,
        batch: Number($("#comparison-batch").value),
      });
      acceptComparison(result);
      if (!pageActive) return;
      if (!activeComparison()) {
        await refreshAll();
        if (model.comparison?.status === "found") notice(`Node ${model.comparison.winner}가 먼저 블록을 찾았습니다 세 노드의 결과를 비교하세요`, "success");
      } else if (token === model.comparisonToken && model.comparisonLooping)
        scheduleComparison(token, 200);
    }, { queued: true });
  }, delay);
}
function comparisonControl(action) {
  stopComparisonLoop();
  const context = comparisonContext();
  return execute(action === "stop" ? "채굴 비교 중지" : "채굴 비교 일시정지", async () => {
    if (!currentComparisonContext(context)) return;
    if (!activeComparison()) { await refreshAll(); return; }
    acceptComparison(await api(`/api/mining/comparison/${action}`, "POST", {
      comparison_id: model.comparison.comparison_id,
    }));
    if (pageActive && context.epoch === model.epoch) await refreshAll();
  }, { queued: true, control: true });
}
function acceptMining(result) {
  const info = result.candidate ?? result;
  if (
    model.mining?.job_id &&
    info.job_id &&
    info.job_id !== model.mining.job_id
  )
    return;
  model.mining = {
    ...model.mining,
    ...info,
    node: model.mining?.node ?? model.node,
  };
  if (result.found) {
    model.mining.status = result.accepted ? "found" : "error";
    model.mining.resultText = result.accepted
      ? `블록 #${result.index} 발견 · Nonce ${result.nonce} · ${number(result.attempts)}회 · ${result.propagation?.length ? "이웃 전파 결과 확인" : "로컬 채굴 · 이웃 전파 없음"}`
      : `블록 검증 거부: ${uiMessage(result.reason)}`;
    stopMineLoop();
    if (result.accepted)
      notice(
        `Node ${model.mining.node}에서 블록 #${result.index} 채굴 완료`,
        "success",
      );
  }
  renderMining();
  renderControls();
  renderGuide();
}
async function ensureMining(mode) {
  if (jobIsActive(model.mining)) return;
  const candidate = (
    await api(`/api/nodes/${model.node}/mine/start`, "POST", {
      miner: $("#mining-wallet").value,
    })
  ).candidate;
  model.mineMode = mode;
  model.selectedSample = null;
  model.mining = { ...candidate, node: model.node };
  renderMining();
}
async function miningStep() {
  await ensureMining("slow");
  if (model.mining.status === "running")
    acceptMining(
      await api(`/api/nodes/${model.mining.node}/mine/pause`, "POST", {
        job_id: model.mining.job_id,
      }),
    );
  const result = await api(
    `/api/nodes/${model.mining.node}/mine/step`,
    "POST",
    { job_id: model.mining.job_id, batch: 1 },
  );
  acceptMining(result);
  if (result.found) await refreshAll();
}
async function startMiningLoop(mode) {
  await ensureMining(mode);
  model.mineMode = mode;
  if (model.mining.status === "paused")
    acceptMining(
      await api(`/api/nodes/${model.mining.node}/mine/resume`, "POST", {
        job_id: model.mining.job_id,
      }),
    );
  stopMineLoop();
  model.looping = true;
  scheduleMine(model.loopToken, 0);
  renderMining();
  renderControls();
}
function scheduleMine(token, delay) {
  model.loopTimer = sleepTimer(() => {
    if (
      token !== model.loopToken ||
      !model.looping ||
      !jobIsActive(model.mining)
    )
      return;
    execute(
      "해시 계산",
      async () => {
        if (token !== model.loopToken || !model.looping) return;
        const job = model.mining;
        const result = await api(`/api/nodes/${job.node}/mine/step`, "POST", {
          job_id: job.job_id,
          batch: model.mineMode === "fast" ? 2000 : 1,
        });
        if (token !== model.loopToken) {
          if (result.found) {
            acceptMining(result);
            await refreshAll();
          }
          return;
        }
        acceptMining(result);
        if (result.found) await refreshAll();
        else if (model.looping && token === model.loopToken)
          scheduleMine(token, model.mineMode === "fast" ? 30 : 250);
      },
      { queued: true },
    );
  }, delay);
}
function miningControl(action) {
  stopMineLoop();
  return execute(
    action === "stop" ? "채굴 중지" : "채굴 일시정지",
    async () => {
      if (!jobIsActive(model.mining)) {
        await refreshAll();
        return;
      }
      acceptMining(
        await api(`/api/nodes/${model.mining.node}/mine/${action}`, "POST", {
          job_id: model.mining.job_id,
        }),
      );
      await refreshAll();
    },
    { queued: true, control: true },
  );
}
async function synchronize(node) {
  const result = await api(`/api/nodes/${node}/sync`, "POST", {});
  await refreshAll();
  invalidateDraft();
  notice(
    result.changed
      ? `Node ${node} 동기화 완료`
      : `Node ${node} · 채택할 새 체인 없음`,
    result.changed ? "success" : "info",
  );
}
function confirmAction(title, description, action, danger = false) {
  if (busy()) return;
  const context = { practiceId: model.practice?.id, epoch: model.epoch };
  model.confirmation = () => action(context);
  $("#confirm-title").textContent = uiMessage(title);
  $("#confirm-description").textContent = uiMessage(description);
  $("#confirm-accept").textContent = danger ? "실행" : "계속";
  $("#confirm-accept").className = danger ? "danger-button" : "";
  $("#confirm-dialog").showModal();
  $("#confirm-cancel").focus();
}
function assertConfirmedContext(context) {
  if (
    context.practiceId !== model.practice?.id ||
    context.epoch !== model.epoch
  )
    throw new ApiError(
      "실습이 바뀌어 실행 취소 · 현재 상태를 확인하세요",
      "PRACTICE_CHANGED",
      409,
    );
}
function requestTamper() {
  if (activeTamper()) {
    stopTamperLoop();
    model.tamperLooping = true;
    scheduleTamper(model.tamperToken, 0);
    return;
  }
  const block_index = strictInteger($("#tamper-block").value, 1);
  const tx_index = strictInteger($("#tamper-tx").value, 0);
  if (block_index === null || tx_index === null) {
    notice("블록과 거래를 선택하세요", "warning");
    return;
  }
  const field = $("#tamper-field").value;
  const mode = $("#tamper-mode").value;
  let value = $("#tamper-value").value.trim();
  if (field === "amount") {
    const n = strictInteger(value, 0);
    if (n === null) {
      notice("새 금액은 0 이상의 정수로 입력하세요", "warning");
      return;
    }
    value = n;
  } else if (!/^[0-9a-fA-F]{64}$/.test(value)) {
    notice("수신 주소는 64자리 16진수로 입력하세요", "warning");
    return;
  }
  const node = model.node;
  const epoch = model.epoch;
  confirmAction(
    `Node ${node} 기록 변경`,
    `${$("#tamper-target").textContent}\n새 ${field === "amount" ? "금액" : "수신 주소"}: ${value}\n방식: ${$("#tamper-mode").selectedOptions[0].textContent}\n\n선택한 노드의 체인만 변경합니다 · 정상 이웃과 동기화해 복구 가능${mode === "remine" ? "\n재채굴은 복사본에서 진행 · 완료 전 취소하면 원본 유지" : ""}`,
    () =>
      execute("변조 실험", async () => {
        if (epoch !== model.epoch || node !== model.node)
          throw new Error("관찰 노드 변경 · 변조 대상을 다시 선택하세요");
        const result = await api(`/api/nodes/${node}/tamper`, "POST", {
          block_index,
          tx_index,
          field,
          value,
          mode,
        });
        invalidateDraft();
        if (result.status === "running") {
          model.tamper = { ...result, node };
          model.tamperLooping = true;
          scheduleTamper(model.tamperToken, 0);
          $("#tamper-result").textContent = "복사본 재채굴 중 · 취소 가능";
        } else {
          model.tamper = { ...result, node, status: "completed" };
          $("#tamper-result").textContent = result.valid
            ? "체인 유효 · 다른 노드의 해시와 작업량을 비교하세요"
            : "변조 검출 · 실패한 검사를 확인하세요";
          await refreshAll();
        }
      }),
    true,
  );
}
function scheduleTamper(token, delay) {
  model.tamperTimer = sleepTimer(() => {
    if (token !== model.tamperToken || !model.tamperLooping || !activeTamper())
      return;
    execute(
      "재채굴 계산",
      async () => {
        if (token !== model.tamperToken) return;
        const result = await api(
          `/api/nodes/${model.tamper.node}/tamper/step`,
          "POST",
          { job_id: model.tamper.job_id, batch: 2000 },
        );
        model.tamper = { ...model.tamper, ...result };
        $("#tamper-result").textContent =
          result.status === "completed"
            ? `재채굴 완료 · ${result.valid ? "체인 유효" : "거래 검증 실패"}`
            : `실제 ${number(result.attempts ?? 0)}회 계산 · 원본 유지 중`;
        if (result.status === "completed") {
          stopTamperLoop();
          await refreshAll();
        } else if (token === model.tamperToken && model.tamperLooping)
          scheduleTamper(token, 30);
      },
      { queued: true },
    );
  }, delay);
}

document.addEventListener("click", (event) => {
  const nav = event.target.closest("[data-page]");
  if (nav) {
    event.preventDefault();
    setPage(nav.dataset.page);
    return;
  }
  const key = event.target.closest("[data-keys]");
  if (key) {
    execute("지갑 키 확인", async () => {
      const r = await api(
        `/api/wallets/${encodeURIComponent(key.dataset.keys)}/keys`,
      );
      $("#keys-title").textContent = `${nameOf(r.address)} · 학습용 지갑 키`;
      $("#wallet-public-key").textContent = r.public_key;
      $("#wallet-private-key").textContent = r.private_key;
      $("#keys-dialog").showModal();
    });
    return;
  }
  const faucet = event.target.closest("[data-faucet]");
  if (faucet) {
    execute("지급 요청", async () => {
      await api("/api/faucet", "POST", {
        address: faucet.dataset.faucet,
        node: model.node,
      });
      await refreshAll();
      notice("지급 등록 완료 · 채굴 후 잔액 반영", "success");
    });
    return;
  }
  const sample = event.target.closest("[data-sample]");
  if (sample) {
    model.selectedSample = structuredClone(
      model.mining?.samples.find(
        (s) => String(s.nonce) === sample.dataset.sample,
      ) ?? null,
    );
    renderMining();
    return;
  }
  const sync = event.target.closest("[data-sync]");
  if (sync) {
    execute("체인 동기화", () => synchronize(sync.dataset.sync));
    return;
  }
  const online = event.target.closest("[data-online]");
  if (online) {
    execute("네트워크 연결 변경", async () => {
      await api(`/api/nodes/${online.dataset.online}/online`, "POST", {
        online: online.dataset.nextOnline === "true",
      });
      invalidateDraft();
      await refreshAll();
      notice(
        online.dataset.nextOnline === "true"
          ? `Node ${online.dataset.online} 온라인 · 기록이 다르면 동기화하세요`
          : `Node ${online.dataset.online} 오프라인 · 로컬 채굴 가능`,
      );
    });
    return;
  }
  const observe = event.target.closest("[data-observe]");
  if (observe) changeNode(observe.dataset.observe);
});
async function changeNode(node) {
  if (busy() || activeWork()) {
    $("#node-select").value = model.node;
    return;
  }
  model.node = node;
  model.epoch++;
  model.mining = null;
  model.selectedSample = null;
  model.tamperTouched = false;
  invalidateDraft();
  await execute("관찰 노드 변경", refreshAll);
}
$("#node-select").addEventListener("change", (event) =>
  changeNode(event.target.value),
);
$("#wallet-form").addEventListener("submit", (event) => {
  event.preventDefault();
  execute("지갑 생성", createWallet);
});
["#tx-from", "#tx-to", "#tx-amount"].forEach((id) =>
  $(id).addEventListener("input", () => {
    model.formTouched = true;
    invalidateDraft("입력 변경 · 거래를 다시 작성하세요");
  }),
);
$("#tx-prepare").addEventListener("click", () =>
  execute("거래 작성", prepareTransaction),
);
$("#tx-sign").addEventListener("click", () =>
  execute("전자서명", signTransaction),
);
$("#tx-submit").addEventListener("click", () =>
  execute("거래 검증", submitTransaction),
);
$("#difficulty-apply").addEventListener("click", () =>
  execute("난이도 적용", async () => {
    const difficulty = strictInteger(
      $("#difficulty").value,
      model.state.min_difficulty,
      model.state.max_difficulty,
    );
    if (difficulty === null)
      throw new Error(
        `난이도는 ${model.state.min_difficulty}~${model.state.max_difficulty} 사이의 정수로 입력하세요`,
      );
    const result = await api("/api/difficulty", "POST", { difficulty });
    stopMineLoop();
    model.mining = null;
    model.selectedSample = null;
    await refreshAll();
    notice(
      `난이도 ${difficulty}를 ${result.from_height != null ? `블록 높이 ${result.from_height}` : "다음 높이의 블록"}부터 적용 · 과거 블록 유지`,
      "success",
    );
  }),
);
$("#mining-step").addEventListener("click", () =>
  execute("한 번 계산", miningStep),
);
$("#mining-slow").addEventListener("click", () =>
  execute("학습 채굴 시작", () => startMiningLoop("slow")),
);
$("#mining-fast").addEventListener("click", () =>
  execute("연속 채굴 시작", () => startMiningLoop("fast")),
);
$("#mining-start").addEventListener("click", () =>
  execute("연속 채굴 시작", () => startMiningLoop("fast")),
);
$("#mining-pause").addEventListener("click", () => miningControl("pause"));
$("#mining-stop").addEventListener("click", () => miningControl("stop"));
$("#mining-resume").addEventListener("click", () =>
  execute("채굴 재개", () => startMiningLoop(model.mineMode)),
);
$("#mining-view-single").addEventListener("click", () => {
  if (activeComparison()) return;
  model.miningView = "single";
  renderMining();
  renderGuide();
});
$("#mining-view-compare").addEventListener("click", () => {
  if (jobIsActive(model.mining) && !activeComparison()) return;
  model.miningView = "compare";
  renderMining();
  renderGuide();
});
$("#comparison-start").addEventListener("click", () => execute("세 노드 비교 시작", startComparisonLoop));
$("#comparison-step").addEventListener("click", () => execute("세 노드 한 번씩 계산", comparisonStep));
$("#comparison-pause").addEventListener("click", () => comparisonControl("pause"));
$("#comparison-stop").addEventListener("click", () => comparisonControl("stop"));
$("#comparison-resume").addEventListener("click", () => execute("채굴 비교 계속", startComparisonLoop));
$("#chain-sync").addEventListener("click", () =>
  execute("체인 동기화", () => synchronize(model.node)),
);
$("#invalid-sync").addEventListener("click", () =>
  execute("현재 노드 복구", () => synchronize(model.node)),
);
$("#tamper-block").addEventListener("change", () => {
  model.tamperTouched = true;
  renderTamperTransactions();
  renderControls();
});
$("#tamper-tx").addEventListener("change", () => {
  model.tamperTouched = true;
  renderTamperTarget();
});
$("#tamper-start").addEventListener("click", requestTamper);
$("#tamper-cancel").addEventListener("click", () => {
  stopTamperLoop();
  execute(
    "재채굴 취소",
    async () => {
      if (!activeTamper()) {
        await refreshAll();
        return;
      }
      await api(`/api/nodes/${model.tamper.node}/tamper/cancel`, "POST", {
        job_id: model.tamper.job_id,
      });
      model.tamper.status = "cancelled";
      $("#tamper-result").textContent = "재채굴 취소 · 원본 유지";
      await refreshAll();
    },
    { queued: true, control: true },
  );
});
$("#refresh").addEventListener("click", () =>
  execute("실습 새로 확인", refreshAll),
);
$("#practice-reset").addEventListener("click", () =>
  confirmAction(
    "실습 초기화",
    "현재 실습의 지갑·거래·블록과 작성 중인 내용을 초기화합니다",
    (context) => {
      stopMineLoop();
      stopComparisonLoop();
      stopTamperLoop();
      execute(
        "현재 실습 초기화",
        async () => {
          assertConfirmedContext(context);
          await api("/api/reset", "POST", {});
          clearTransient();
          model.node = "A";
          await refreshAll();
          notice("실습 초기화 완료");
        },
        { queued: true, control: true },
      );
    },
    true,
  ),
);
$("#practice-new").addEventListener("click", () =>
  confirmAction(
    "새 실습 시작",
    "작성 중인 내용과 계산을 지우고 새 실습으로 전환합니다",
    (context) => {
      stopMineLoop();
      stopComparisonLoop();
      stopTamperLoop();
      execute(
        "새 실습 시작",
        async () => {
          assertConfirmedContext(context);
          await api(
            "/api/practices/new",
            "POST",
            {},
            { allowPracticeChange: true },
          );
          clearTransient();
          model.node = "A";
          await refreshAll();
          setPage("home");
          notice("새 실습 준비 완료", "success");
        },
        { queued: true, control: true },
      );
    },
  ),
);
$("#confirm-cancel").addEventListener("click", () => {
  model.confirmation = null;
  $("#confirm-dialog").close();
});
$("#confirm-dialog").addEventListener("cancel", () => {
  model.confirmation = null;
});
$("#confirm-accept").addEventListener("click", () => {
  const action = model.confirmation;
  model.confirmation = null;
  $("#confirm-dialog").close();
  if (action) action();
});
$("#keys-close").addEventListener("click", () => $("#keys-dialog").close());
$("#keys-dialog").addEventListener("close", () => {
  $("#wallet-public-key").textContent = "";
  $("#wallet-private-key").textContent = "";
});
$("#theme-select").value = window.teamLabTheme.getPreference();
$("#theme-select").addEventListener("change", (event) => {
  window.teamLabTheme.select(event.target.value);
});
window.addEventListener("team-theme-change", () => {
  $("#theme-select").value = window.teamLabTheme.getPreference();
});
$("#glossary-open").addEventListener("click", () => window.openDict?.());
$("#events-filter").addEventListener("change", renderEvents);
$("#events-export").addEventListener("click", () => {
  const blob = new Blob([JSON.stringify(model.events, null, 2)], {
    type: "application/json",
  });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "blockchain-learning-events.json";
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});
$("#guide-next").addEventListener("click", () => {
  const destination = $("#guide-next").dataset.destination;
  if (destination !== model.page) setPage(destination);
  else {
    const target = {
      home: "#wallet-name",
      wallet: "#wallet-name",
      transaction:
        model.txStage === "prepared"
          ? "#tx-sign"
          : model.txStage === "signed"
            ? "#tx-submit"
            : "#tx-from",
      mining: model.miningView === "compare"
        ? activeComparison() ? model.comparisonLooping ? "#comparison-pause" : "#comparison-resume" : "#comparison-start"
        : jobIsActive(model.mining) ? "#mining-pause" : "#mining-step",
      chain: "#tamper-block",
      network: "#network-list",
      events: "#events-filter",
      mempool: "#mempool-list",
    }[destination];
    $(target)?.scrollIntoView({ behavior: "smooth", block: "center" });
    if ($(target)?.focus) $(target).focus();
  }
});
window.addEventListener("hashchange", () => {
  const page = location.hash.slice(1);
  if (pages[page]) setPage(page, false);
});
window.addEventListener("pagehide", () => {
  pageActive = false;
  clearTimeout(recoveryTimer);
  recoveryTimer = null;
  stopMineLoop();
  stopComparisonLoop();
  stopTamperLoop();
});
window.addEventListener("offline", () => beginConnectionRecovery());
window.addEventListener("online", () => {
  if (!model.serverReady) {
    clearTimeout(recoveryTimer);
    recoveryTimer = null;
    recoveryDelay = 0;
    beginConnectionRecovery();
  }
});
window.addEventListener("pageshow", () => {
  pageActive = true;
  if (!model.serverReady && !busy()) beginConnectionRecovery();
});
setPage(location.hash.slice(1) || "home", false);
renderControls();
execute("실습 불러오기", refreshAll);
