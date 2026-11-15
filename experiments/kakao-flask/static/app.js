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
  home: [
    "시작하기",
    "LEARN BY DOING",
    "기록이 신뢰가 되는 과정을 배워요.",
    "지갑에서 시작해 거래와 채굴, 세 노드의 검증까지 직접 살펴보세요.",
  ],
  wallet: [
    "지갑",
    "01 / WALLET",
    "지갑은 코인 대신, 열쇠를 보관해요.",
    "주소와 키를 확인하고 학습용 코인을 받을 지갑을 준비하세요.",
  ],
  transaction: [
    "거래·전자서명",
    "02 / TRANSACTION",
    "작성한 내용에 나만의 서명을 더해요.",
    "거래 작성 → 전자서명 → 노드 검증·제출을 한 단계씩 진행하세요.",
  ],
  mempool: [
    "거래 대기열",
    "03 / MEMPOOL",
    "검증을 마친 거래도, 아직은 대기 중.",
    "지금 관찰하는 노드에서 블록에 들어갈 거래를 확인하세요.",
  ],
  mining: [
    "채굴",
    "04 / PROOF OF WORK",
    "숫자 하나가 바꾸는 해시를 관찰해요.",
    "실제 Nonce를 계산하고, 목표 조건을 만족하는 순간을 찾아보세요.",
  ],
  chain: [
    "블록·변조 실험",
    "05 / BLOCKCHAIN",
    "앞 기록의 지문으로 연결된 장부.",
    "검증 결과를 비교하고, 내용을 바꿨을 때 어떤 검사가 실패하는지 살펴보세요.",
  ],
  network: [
    "노드·동기화",
    "06 / NETWORK",
    "서로 다른 장부가 같은 기록을 고르는 법.",
    "노드를 네트워크에서 분리하고, 다시 연결한 뒤 동기화해 보세요.",
  ],
  events: [
    "활동 기록",
    "07 / ACTIVITY",
    "지금까지의 실습을 돌아봐요.",
    "거래와 채굴, 검증 실패와 복구가 실제로 일어난 순서를 확인하세요.",
  ],
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
};
const number = (n) =>
  Number.isFinite(n) ? n.toLocaleString("ko-KR") : String(n ?? "—");
const short = (s, n = 12) => (s ? String(s).slice(0, n) + "…" : "—");
const nameOf = (address) =>
  address === "SYSTEM" || !address
    ? "시스템"
    : (model.wallets.find((w) => w.address === address)?.name ??
      short(address));
const currentNode = () => model.state?.nodes?.find((n) => n.id === model.node);
const busy = () => model.pending > 0;
const activeTamper = () =>
  Boolean(model.tamper && model.tamper.status === "running");
const activeWork = () => jobIsActive(model.mining) || activeTamper();
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
  el.textContent = text;
  el.className = `notice ${kind}`;
  el.hidden = !text;
}
function stopMineLoop() {
  model.loopToken++;
  clearTimeout(model.loopTimer);
  model.loopTimer = null;
  model.looping = false;
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
  stopTamperLoop();
  model.mining = null;
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
      "다른 탭에서 실습 공간이 바뀌었습니다. 현재 실습을 다시 확인합니다.",
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
  const response = await fetch(path, {
    method,
    credentials: "same-origin",
    cache: "no-store",
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await response.json().catch(() => {
    throw new ApiError(
      "서버 응답을 읽지 못했습니다. 서버 상태를 확인한 뒤 다시 시도해 주세요.",
      "BAD_RESPONSE",
      response.status,
    );
  });
  if (epoch !== model.epoch) throw new Superseded("다른 실습의 응답");
  if (!response.ok)
    throw new ApiError(
      data.reason || data.error || "요청을 처리하지 못했습니다.",
      data.code,
      response.status,
    );
  rememberMeta(data, response, options.allowPracticeChange);
  if (data?.ok === false)
    throw new ApiError(
      data.reason || data.error || "요청이 거부됐습니다.",
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
        notice(
          error instanceof TypeError
            ? "서버에 연결할 수 없습니다. Python 서버가 실행 중인지 확인하고 ‘새로 확인’을 눌러 주세요."
            : error.message,
          "danger",
        );
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
    notice(
      "이 브라우저의 다른 실습 공간을 불러왔습니다. 준비한 거래와 화면 선택은 초기화했습니다.",
    );
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
      "다른 탭에서 실습이 바뀌고 있습니다. 잠시 후 새로 확인해 주세요.",
      "STATE_CHANGED",
      409,
    );
  }
  model.state = state;
  model.wallets = wallets;
  model.mempool = mempool;
  model.chain = chain;
  model.events = events;
  if (
    model.tx?.signed &&
    ["submitted", "confirmed", "unconfirmed"].includes(model.txStage)
  ) {
    const txid = model.tx.signed.txid;
    const includedAt = confirmedBlockIndex(chain, txid);
    if (includedAt !== null) {
      model.txStage = "confirmed";
      model.txFeedback = `블록 #${includedAt}에 포함돼 Node ${node}의 장부와 확정 잔액에 반영됐습니다. 다른 노드의 반영 상태도 비교해 보세요.`;
    } else if (chain.valid && mempool.some((tx) => tx.txid === txid)) {
      model.txStage = "submitted";
      model.txFeedback =
        "검증을 통과해 거래 대기열에 등록돼 있습니다. 아직 이 노드의 블록에는 포함되지 않았습니다.";
    } else if (
      model.txStage === "confirmed" ||
      model.txStage === "unconfirmed" ||
      !chain.valid
    ) {
      model.txStage = "unconfirmed";
      model.txFeedback = chain.valid
        ? "이 노드의 현재 체인에서 해당 거래를 확인할 수 없습니다. 대기열과 노드 상태를 비교해 주세요."
        : "체인 검증이 실패해 이 거래의 확정 상태를 신뢰할 수 없습니다. 정상 체인과 동기화해 주세요.";
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
  if (activeElsewhere && !jobIsActive(model.mining)) {
    notice(
      `Node ${activeElsewhere.id}에 진행 중인 채굴 후보가 있습니다. 해당 노드에서 이어 보거나 중지할 수 있습니다.`,
    );
  }
  const recovery =
    state.practice?.recovery_notice ||
    (state.practice?.job_cancelled_on_restore
      ? "서버 재시작으로 진행 중이던 계산을 중지했습니다. 저장된 체인과 지갑은 복원됐습니다."
      : "");
  if (
    recovery &&
    !model.recoveryNotices.has(`${model.practice?.id}:${recovery}`)
  ) {
    notice(recovery);
    model.recoveryNotices.add(`${model.practice?.id}:${recovery}`);
  }
  renderAll();
}
function renderSaveStatus(label = "") {
  const p = model.practice;
  $("#practice-label").textContent = p
    ? `내 실습 · ${String(p.id).slice(0, 8)}`
    : "내 실습을 불러오는 중";
  $("#save-dot").className =
    `save-dot${model.saveProblem ? " failed" : busy() ? " pending" : ""}`;
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
      ? "이 브라우저의 실습 자동 저장됨"
      : `자동 저장됨 · ${date.toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" })}`;
  } else
    $("#save-status").textContent = p
      ? "이 브라우저의 실습 자동 저장"
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
  const [title, eyebrow, heading, description] = pages[page];
  $("#breadcrumb").textContent = title;
  $("#page-eyebrow").textContent = eyebrow;
  $("#page-title").textContent = heading;
  $("#page-description").textContent = description;
  document.title = `${title} · 블록체인 실습실`;
  const guide = $("#learning-guide");
  const container = $(`#page-${page}`);
  if (page === "home")
    container.insertBefore(guide, container.querySelector(".home-grid"));
  else container.prepend(guide);
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
      "거래 하나가 기록되는 여정",
      users.length < 2
        ? "Alice와 Bob의 지갑을 만들어 첫 지급 거래를 준비하세요."
        : "준비한 지갑으로 송금 거래를 작성해 보세요.",
      "서명과 검증을 거친 거래가 블록에 들어가고 세 노드에 반영됩니다.",
      users.length < 2 ? "wallet" : "transaction",
      users.length < 2 ? "지갑 만들러 가기 →" : "거래 작성하러 가기 →",
    ],
    wallet: [
      "지갑의 숫자는 어디에서 올까요?",
      users.length < 2
        ? "이름을 입력하고 Alice·Bob 지갑을 하나씩 만들어 보세요."
        : hasPending
          ? "새 지갑의 지급 거래가 기다립니다. 첫 블록을 채굴해 보세요."
          : "지갑의 확정 잔액과 주소를 확인하고 송금을 준비하세요.",
      "지갑 생성 직후에는 대기 입금 +50이며, 블록에 기록되면 확정 잔액이 됩니다.",
      hasPending ? "mempool" : users.length < 2 ? "wallet" : "transaction",
      hasPending
        ? "대기 거래 확인 →"
        : users.length < 2
          ? "지갑 이름 입력으로 →"
          : "송금 준비하기 →",
    ],
    transaction: [
      "서명은 작성한 내용에 묶입니다.",
      model.txStage === "signed"
        ? "서명 내용을 확인한 뒤 검증·제출을 누르세요."
        : model.txStage === "confirmed"
          ? "현재 노드의 블록에 반영됐습니다. 다른 노드의 장부와 잔액도 비교하세요."
          : model.txStage === "submitted"
            ? "검증을 통과한 거래를 대기열에서 확인하세요."
            : model.txStage === "prepared"
              ? "작성한 송신자·수신자·금액을 읽고 개인키로 서명하세요."
              : "보내는 지갑·받는 지갑·금액을 정하고 거래 작성부터 시작하세요.",
      "서명 후 내용을 바꾸면 검증에 실패합니다. 정상 제출도 채굴 전에는 확정 잔액을 바꾸지 않습니다.",
      model.txStage === "confirmed"
        ? "network"
        : model.txStage === "submitted"
          ? "mempool"
          : "transaction",
      model.txStage === "confirmed"
        ? "다른 노드와 비교 →"
        : model.txStage === "submitted"
          ? "대기 거래 확인 →"
          : "현재 거래 단계로 →",
    ],
    mempool: [
      "검증과 블록 확정은 다른 단계예요.",
      hasPending
        ? "대기 거래의 금액과 송신자를 확인한 뒤 채굴을 시작하세요."
        : "선택한 노드의 대기열이 비어 있습니다. 지갑을 만들거나 새 거래를 제출하세요.",
      "검증된 거래는 블록에 담기기를 기다립니다. 채굴이 성공하면 대기열에서 빠지고 장부에 남습니다.",
      hasPending ? "mining" : users.length < 2 ? "wallet" : "transaction",
      hasPending ? "이 거래로 채굴하기 →" : "새 거래 준비하기 →",
    ],
    mining: [
      "Nonce 하나로 해시가 달라집니다.",
      jobIsActive(model.mining)
        ? model.mining.status === "paused"
          ? "일시정지한 표본을 비교하거나 한 번 더 계산해 보세요."
          : "필요하면 일시정지한 뒤 표본을 골라 비교해 보세요."
        : model.mining?.status === "found"
          ? "성공한 블록이 다른 노드에도 반영됐는지 비교하세요."
          : "한 번 계산하기로 첫 해시를 보고, 천천히 학습 실행을 눌러 보세요.",
      "목표 개수의 0으로 시작하는 실제 해시를 찾을 때만 블록이 추가됩니다. 서버 계산 시간과 화면 대기는 구분됩니다.",
      model.mining?.status === "found" ? "network" : "mining",
      model.mining?.status === "found" ? "노드 반영 비교 →" : "채굴 조작으로 →",
    ],
    chain: [
      "기록을 고치면 무엇이 어긋날까요?",
      !valid
        ? "변조로 체인 검증이 실패했습니다. 실패 항목을 읽고 유효한 이웃과 동기화하세요."
        : (model.chain?.height ?? 0) < 1
          ? "지급 거래나 송금 거래가 담긴 블록을 먼저 채굴하세요."
          : "변조할 블록·거래·새 값을 고르고, 확인창에서 대상을 다시 읽으세요.",
      "서명된 송금은 내용을 고친 뒤 PoW를 다시 수행해도 거래 검증을 통과할 수 없습니다.",
      !valid ? "network" : (model.chain?.height ?? 0) < 1 ? "mining" : "chain",
      !valid
        ? "정상 노드와 비교·복구 →"
        : (model.chain?.height ?? 0) < 1
          ? "먼저 블록 채굴하기 →"
          : "변조 실험으로 →",
    ],
    network: [
      "네트워크 연결과 로컬 연산은 달라요.",
      "한 노드를 오프라인으로 분리해 채굴하고, 다시 온라인으로 연결한 뒤 동기화해 보세요.",
      "분리된 노드의 블록은 이웃에게 전파되지 않습니다. 동기화는 유효한 체인의 누적 작업량을 비교합니다.",
      "network",
      "노드 상태 비교하기 →",
    ],
    events: [
      "실패와 복구도 학습의 기록입니다.",
      "기록 유형을 바꾸며 거래 검증, 채굴, 동기화가 어떤 순서로 일어났는지 읽어 보세요.",
      "실패 이유와 대상 노드를 함께 읽으면 다음에 바꿀 조건을 찾을 수 있습니다.",
      "events",
      "최근 기록 살펴보기 →",
    ],
  };
  const guide = guides[model.page];
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
      ? "이 노드의 체인 검증이 실패해 잔액을 신뢰할 수 없습니다. 동기화 후 거래를 준비하세요."
      : `${wallet.name} · 확정 ${number(wallet.balance)}코인 · 대기 출금 ${number(wallet.pending_out ?? 0)}코인`
    : "먼저 지갑을 만들어 주세요.";
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
    $("#tx-summary").textContent =
      "거래를 작성하면 금액과 거래 순번을 확인할 수 있습니다.";
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
  $("#tx-result").textContent =
    model.txFeedback ||
    (tx?.signed
      ? "전자서명을 생성했습니다. 검증·제출로 노드의 판단을 확인하세요."
      : "아직 서명하지 않았습니다.");
}
function renderMempool() {
  $("#mempool-node").textContent = `· Node ${model.node}`;
  const n = currentNode();
  $("#mempool-metrics").innerHTML =
    `<div><span>현재 노드</span><strong>Node ${model.node}</strong><small>${n?.online ? "네트워크 연결됨" : "네트워크에서 분리됨"}</small></div><div><span>대기 거래</span><strong>${model.mempool.length}<em>건</em></strong><small>아직 블록 포함 전</small></div><div><span>현재 체인 높이</span><strong>${number(n?.height ?? 0)}</strong><small>Genesis 다음 블록부터 집계</small></div>`;
  $("#mempool-list").innerHTML = model.mempool.length
    ? model.mempool
        .map(
          (t) =>
            `<tr><td><span class="tiny-tag">${esc({ faucet: "지급", transfer: "송금", coinbase: "보상" }[t.type] ?? t.type)}</span></td><td>${esc(nameOf(t.sender_address))} → ${esc(nameOf(t.receiver_address))}</td><td class="numeric"><strong>${number(t.amount)}</strong></td><td>${number(t.nonce)}</td><td class="mono" title="${esc(t.txid)}">${esc(short(t.txid, 17))}</td></tr>`,
        )
        .join("")
    : '<tr><td colspan="5"><div class="empty-state">대기 중인 거래가 없습니다. 지갑을 만들거나 새 송금 거래를 제출해 보세요.</div></td></tr>';
}
function hashHtml(hash, difficulty) {
  const value = String(hash ?? "");
  const zeros = Math.min(Number(difficulty) || 0, value.match(/^0*/)[0].length);
  return `<mark>${esc(value.slice(0, zeros))}</mark>${esc(value.slice(zeros))}`;
}
function renderMining() {
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
    : "첫 번째 계산을 기다리고 있습니다.";
  $("#mining-attempts").textContent = number(job?.attempts ?? 0);
  $("#mining-elapsed").innerHTML =
    `${((job?.elapsed_ms ?? 0) / 1000).toFixed(3)} <em>초</em>`;
  $("#mining-reward").innerHTML =
    `+${number(model.state?.block_reward ?? 10)} <em>코인</em>`;
  $("#mining-result").textContent =
    job?.resultText ??
    (job?.reason === "restore_cancelled"
      ? "서버 재시작으로 이전 계산은 중지했습니다. 저장된 체인은 유지됩니다."
      : job?.status === "paused"
        ? "일시정지했습니다. 같은 후보에서 한 번 더 계산하거나 이어갈 수 있습니다."
        : job?.status === "stopped"
          ? "후보 계산을 중지했습니다. 새 채굴은 새로운 후보로 시작합니다."
          : "");
  const actualDifficulty = jobIsActive(job)
    ? job.difficulty
    : (currentNode()?.difficulty ?? model.state?.difficulty ?? 2);
  const modeHint = jobIsActive(job)
    ? model.mineMode === "fast"
      ? "빠른 실행 · 요청마다 최대 2000회 계산합니다."
      : "학습 실행 · 약 250ms 간격으로 하나씩 계산합니다."
    : "한 번 계산하거나 천천히 학습 실행으로 관찰해 보세요.";
  $("#mining-mode-hint").textContent =
    `이 노드의 후보 난이도 ${actualDifficulty} · 목표 ${"0".repeat(actualDifficulty)}… · ${modeHint}`;
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
    : "아직 계산 표본이 없습니다.";
  $("#selected-sample").hidden = !model.selectedSample;
  if (model.selectedSample)
    $("#selected-sample").innerHTML =
      `<strong>선택한 표본 · Nonce ${esc(model.selectedSample.nonce)}</strong><code>${esc(model.selectedSample.hash)}</code><p>최신 계산과 별도로 관찰하는 실제 표본입니다. 다음 계산이 진행돼도 선택한 값은 유지됩니다.</p>`;
}
function renderChain() {
  const chain = model.chain;
  $("#chain-status").innerHTML =
    `<span class="status-chip ${chain?.valid ? "ok" : "bad"}">${chain?.valid ? "✓ 모든 블록의 검증을 통과했습니다" : "검증 실패 · 이 노드의 잔액은 신뢰할 수 없습니다"}</span>`;
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
          )}</div><dl><dt>블록 해시</dt><dd>${esc(b.hash)}</dd><dt>앞 블록 해시</dt><dd>${esc(b.previous_hash)}</dd><dt>Merkle Root</dt><dd>${esc(b.merkle_root)}</dd><dt>Nonce · 난이도</dt><dd>${number(b.nonce)} · ${number(b.difficulty)}</dd></dl>${b.transactions.length ? `<details><summary>거래 ${b.transactions.length}개 보기</summary>${b.transactions.map((t, i) => `<div class="tx-line">#${i} ${esc({ coinbase: "채굴 보상", faucet: "지급", transfer: "송금" }[t.type] ?? t.type)}<br>${esc(nameOf(t.sender_address))} → ${esc(nameOf(t.receiver_address))} · <b>${number(t.amount)}</b></div>`).join("")}</details>` : ""}${b.reasons?.length ? `<p class="reason">${b.reasons.map(esc).join("<br>")}</p>` : ""}</article>`,
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
    : '<option value="">먼저 블록을 채굴해 주세요</option>';
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
    : '<option value="">선택할 거래가 없습니다</option>';
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
    : "채굴한 블록이 있어야 변조 실험을 시작할 수 있습니다.";
}
function renderNetwork() {
  $("#network-list").innerHTML = (model.state?.nodes ?? [])
    .map(
      (n) =>
        `<article class="network-card${n.id === model.node ? " observed" : ""}"><h2>${esc(n.name)}${n.id === model.node ? '<span class="tiny-tag">관찰 중</span>' : ""}</h2><span class="connection${n.online ? "" : " offline"}">${n.online ? "네트워크 연결됨" : "네트워크에서 분리됨"}</span><div class="height">${number(n.height)} <small>블록 높이</small></div><dl><dt>마지막 블록 해시</dt><dd class="mono">${esc(n.tip_hash)}</dd><dt>검증 · 대기 거래</dt><dd class="${n.valid ? "ok" : "bad"}">${n.valid ? "✓ 체인 유효" : "× 변조 검출"} · ${n.mempool}건 대기</dd><dt>누적 작업량 · 채굴 상태</dt><dd>${esc(n.cumulative_work ?? "—")} · ${esc({ running: "계산 후보 준비됨", paused: "일시정지", found: "발견 완료", stopped: "중지됨", idle: "준비" }[n.mining_status] ?? "준비")}</dd></dl><div class="actions"><button class="secondary" data-online="${esc(n.id)}" data-next-online="${!n.online}">${n.online ? "오프라인으로 분리" : "온라인으로 연결"}</button><button class="secondary" data-sync="${esc(n.id)}" data-testid="node-sync-${esc(n.id)}" ${n.online ? "" : "disabled"}>동기화</button><button class="text-button" data-observe="${esc(n.id)}">이 노드 관찰 →</button></div></article>`,
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
  $("#events-count").textContent =
    `${selected.length}개 기록 · 최근 기록이 위에 표시됩니다`;
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
          `<li class="kind-${kinds.has(e.kind) ? e.kind : "sys"}"><time>${esc(e.time)}</time><strong>${e.node === "-" ? "실습실" : `Node ${esc(e.node)}`}</strong><p>${esc(e.msg)}</p></li>`,
      )
      .join("") || "<li><p>아직 이 유형의 기록이 없습니다.</p></li>";
}
function renderControls() {
  const ready = Boolean(model.state);
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
  set("#refresh", busy());
  set("#practice-new", busy());
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
  const controlsLocked = !ready || model.controlPending || activeTamper();
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
      mining ||
      (activeTamper() && model.tamperLooping) ||
      !$("#tamper-block").value,
  );
  $("#tamper-start").textContent = activeTamper()
    ? "재채굴 계산 이어가기"
    : "대상 확인 후 변조";
  set("#tamper-cancel", !activeTamper() || model.controlPending);
  set("#chain-sync", locked || !currentNode()?.online);
  set("#invalid-sync", locked || !currentNode()?.online);
  $("#integrity-warning").hidden = !ready || validChain;
  $("#integrity-description").textContent =
    `Node ${model.node}의 체인 검증이 실패했습니다. 잔액을 신뢰할 수 없으므로 정상 이웃 체인과 비교·동기화한 뒤 거래와 채굴을 진행하세요.${currentNode()?.online ? "" : " 먼저 노드를 온라인으로 연결해 주세요."}`;
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
    $("#task-message").textContent = activeTamper()
      ? `Node ${model.tamper.node ?? model.node}의 복사본을 재채굴하고 있습니다. 취소하면 원래 체인을 유지합니다. 다른 설정은 완료·취소 후 바꿀 수 있습니다.`
      : `Node ${model.mining.node}의 채굴 후보를 ${model.mining.status === "paused" ? "일시정지해 관찰 중입니다" : model.looping ? "계산 중입니다" : "불러왔습니다"}. 노드와 조건은 작업 중지 후 바꿀 수 있습니다.`;
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
    notice("지갑 이름을 입력해 주세요.", "warning");
    return;
  }
  const result = await api("/api/wallets", "POST", { name, node: model.node });
  $("#wallet-name").value = "";
  $("#wallet-feedback").textContent = result.faucet?.ok
    ? `${name} 지갑을 만들었습니다. 현재 지급 상태와 확정 잔액은 아래 목록에서 확인하세요.`
    : `${name} 지갑을 만들었습니다. 지급 요청 결과: ${result.faucet?.reason ?? "대기열을 확인해 주세요."}`;
  await refreshAll();
  notice(
    `${name} 지갑을 생성했습니다. 채굴 후 지급액이 확정됩니다.`,
    "success",
  );
}
async function prepareTransaction() {
  const input = txInput();
  const amount = strictInteger(input.amount);
  if (!input.from || !input.to)
    throw new Error("송신 지갑과 수신 지갑을 먼저 선택해 주세요.");
  if (amount === null)
    throw new Error("송금액은 1 이상의 정확한 정수로 입력해 주세요.");
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
  model.txFeedback =
    "거래 내용을 준비했습니다. 송신자·수신자·금액을 읽고 서명하세요.";
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
    throw new Error("현재 입력으로 거래를 다시 작성해 주세요.");
  const result = await api("/api/tx/sign", "POST", { tx: tx.draft });
  if (generation !== model.txGeneration || model.tx !== tx) return;
  tx.signed = result.tx;
  model.txStage = "signed";
  model.txFeedback =
    "전자서명을 생성했습니다. 검증·제출로 노드의 판단을 확인하세요.";
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
    throw new Error(
      "입력이 바뀌었습니다. 거래 작성과 서명부터 다시 진행해 주세요.",
    );
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
  model.txFeedback =
    "검증을 통과해 거래 대기열에 등록됐습니다. 아직 확정 잔액은 바뀌지 않습니다.";
  await refreshAll();
  renderTransaction();
  notice(
    model.txStage === "confirmed"
      ? "서명된 거래가 블록에 반영됐습니다. 현재 잔액과 노드 상태를 확인해 보세요."
      : "서명된 거래를 등록했습니다. 대기열을 확인하고 채굴해 보세요.",
    "success",
  );
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
      ? `블록 #${result.index}을 찾았습니다. Nonce ${result.nonce} · ${number(result.attempts)}회 계산. ${result.propagation?.length ? "이웃 노드 전파 결과를 비교해 보세요." : "네트워크에서 분리된 노드라면 이 블록은 로컬에만 남습니다."}`
      : `블록 검증 거부: ${result.reason}`;
    stopMineLoop();
    if (result.accepted)
      notice(
        `Node ${model.mining.node}에서 블록 #${result.index} 채굴을 완료했습니다. 현재 확정 잔액과 노드 반영 결과를 확인해 보세요.`,
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
      ? `Node ${node}를 유효한 체인으로 동기화했습니다.`
      : `Node ${node}에 채택할 더 높은 작업량의 유효한 체인이 없습니다.`,
    result.changed ? "success" : "info",
  );
}
function confirmAction(title, description, action, danger = false) {
  if (busy()) return;
  const context = { practiceId: model.practice?.id, epoch: model.epoch };
  model.confirmation = () => action(context);
  $("#confirm-title").textContent = title;
  $("#confirm-description").textContent = description;
  $("#confirm-accept").textContent = danger ? "확인하고 실행" : "계속";
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
      "확인한 실습 공간이 바뀌어 작업을 실행하지 않았습니다. 현재 실습을 다시 확인해 주세요.",
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
    notice("변조할 블록과 거래를 선택해 주세요.", "warning");
    return;
  }
  const field = $("#tamper-field").value;
  const mode = $("#tamper-mode").value;
  let value = $("#tamper-value").value.trim();
  if (field === "amount") {
    const n = strictInteger(value, 0);
    if (n === null) {
      notice("새 금액을 0 이상의 정수로 입력해 주세요.", "warning");
      return;
    }
    value = n;
  } else if (!/^[0-9a-fA-F]{64}$/.test(value)) {
    notice("수신 주소는 64자리 16진수로 입력해 주세요.", "warning");
    return;
  }
  const node = model.node;
  const epoch = model.epoch;
  confirmAction(
    `Node ${node}의 기록을 바꿀까요?`,
    `${$("#tamper-target").textContent}\n새 ${field === "amount" ? "금액" : "수신 주소"}: ${value}\n방식: ${$("#tamper-mode").selectedOptions[0].textContent}\n\n선택한 노드의 체인만 변경합니다. 정상 이웃이 있다면 동기화로 복구할 수 있습니다.${mode === "remine" ? "\n재채굴은 복사본에서 진행하며, 완료 전 취소하면 원래 체인이 유지됩니다." : ""}`,
    () =>
      execute("변조 실험", async () => {
        if (epoch !== model.epoch || node !== model.node)
          throw new Error(
            "관찰 대상이 바뀌었습니다. 변조 대상을 다시 확인해 주세요.",
          );
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
          $("#tamper-result").textContent =
            "복사본을 재채굴하고 있습니다. 계산 중에도 취소할 수 있습니다.";
        } else {
          model.tamper = { ...result, node, status: "completed" };
          $("#tamper-result").textContent = result.valid
            ? "이 체인은 현재 검사에서 유효합니다. 다른 노드의 마지막 해시와 작업량도 비교하세요."
            : "변조를 검출했습니다. 실패 항목을 읽고 노드 화면에서 정상 체인과 비교해 보세요.";
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
            ? `재채굴을 완료했습니다. ${result.valid ? "체인은 유효합니다. 다른 노드와 비교하세요." : "PoW를 다시 수행해도 거래 검증을 통과하지 못했습니다."}`
            : `실제 ${number(result.attempts ?? 0)}회 계산 중 · 원래 체인은 아직 변경하지 않았습니다.`;
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
      notice(
        "지급 거래를 대기열에 등록했습니다. 채굴 후 잔액에 반영됩니다.",
        "success",
      );
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
          ? `Node ${online.dataset.online}를 연결했습니다. 기록이 다르면 직접 동기화하세요.`
          : `Node ${online.dataset.online}를 네트워크에서 분리했습니다. 로컬 채굴은 계속 실습할 수 있습니다.`,
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
    invalidateDraft(
      "입력이 바뀌었습니다. 현재 내용으로 거래를 다시 작성해 주세요.",
    );
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
        `난이도는 ${model.state.min_difficulty}~${model.state.max_difficulty} 사이의 정수로 입력해 주세요.`,
      );
    const result = await api("/api/difficulty", "POST", { difficulty });
    stopMineLoop();
    model.mining = null;
    model.selectedSample = null;
    await refreshAll();
    notice(
      `난이도 ${difficulty}를 ${result.from_height != null ? `블록 높이 ${result.from_height}` : "다음 높이의 블록"}부터 적용합니다. 과거 블록의 규칙은 바뀌지 않습니다.`,
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
      $("#tamper-result").textContent =
        "재채굴 실험을 취소했습니다. 원래 체인을 유지했습니다.";
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
    "현재 실습을 초기화할까요?",
    "이 브라우저의 현재 지갑·체인·대기 거래를 초기 상태로 되돌립니다. 다른 브라우저의 실습은 바뀌지 않습니다. 준비한 서명과 선택한 표본도 지워집니다.",
    (context) => {
      stopMineLoop();
      stopTamperLoop();
      execute(
        "현재 실습 초기화",
        async () => {
          assertConfirmedContext(context);
          await api("/api/reset", "POST", {});
          clearTransient();
          model.node = "A";
          await refreshAll();
          notice("현재 실습을 초기화했습니다. 새 지갑부터 준비해 보세요.");
        },
        { queued: true, control: true },
      );
    },
    true,
  ),
);
$("#practice-new").addEventListener("click", () =>
  confirmAction(
    "새 실습 공간을 시작할까요?",
    "새로운 지갑과 체인을 준비합니다. 현재 화면의 거래·서명·계산 선택을 지우고 이 브라우저를 새 실습 공간으로 전환합니다.",
    (context) => {
      stopMineLoop();
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
          notice(
            "새 실습 공간을 준비했습니다. 지갑 생성부터 시작하세요.",
            "success",
          );
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
$("#theme-select").value = document.documentElement.dataset.theme;
$("#theme-select").addEventListener("change", (event) => {
  document.documentElement.dataset.theme = event.target.value;
  try {
    localStorage.setItem("blockchain-lab-theme", event.target.value);
  } catch {
    /* Theme remains usable without storage. */
  }
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
      mining: jobIsActive(model.mining) ? "#mining-pause" : "#mining-step",
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
  stopMineLoop();
  stopTamperLoop();
});
setPage(location.hash.slice(1) || "home", false);
renderControls();
execute("내 실습 불러오기", refreshAll);
