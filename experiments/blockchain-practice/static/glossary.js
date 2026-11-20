/* 용어 도움말: 점선 밑줄 용어에 마우스를 올리면 설명이 뜨고, openDict()로 전체 용어 사전을 연다. */
(function () {
  const GROUPS = [
    "기본 개념",
    "지갑·암호",
    "거래",
    "대기열·채굴",
    "블록·체인",
    "네트워크·무결성",
  ];
  const G = [
    [
      "blockchain",
      0,
      "블록체인 (Blockchain)",
      "거래 묶음인 블록을 앞 블록의 해시로 연결한 장부로, 중간 기록을 고치면 해시와 연결 검증이 실패해요",
    ],
    [
      "node",
      0,
      "노드 (Node)",
      "각자 장부와 대기열을 갖고 거래·블록을 검증하는 참여자로, 이 앱은 한 서버 안에 모의 노드 3개를 둬요",
    ],
    [
      "wallet",
      1,
      "지갑 (Wallet)",
      "개인키·공개키·주소를 묶어 관리하며, 잔액은 장부에서 계산해요",
    ],
    [
      "private",
      1,
      "개인키 (Private Key)",
      "거래에 서명하는 비밀키로, 이 앱은 서버가 보관하고 키 보기를 눌러야 표시해요 — 교육용으로만 사용하세요",
    ],
    [
      "public",
      1,
      "공개키 (Public Key)",
      "개인키와 짝을 이루며 서명 검증에 쓰는 키로, 공개해도 개인키를 쉽게 알아낼 수 없어요",
    ],
    [
      "address",
      1,
      "주소 (Address)",
      "코인을 받을 때 쓰는 식별자로, 이 앱은 공개키의 SHA-256 해시를 사용해요 — 실제 비트코인의 주소 형식과는 달라요",
    ],
    [
      "hash",
      1,
      "해시 (Hash)",
      "데이터를 고정 길이의 지문으로 바꾸는 계산으로, SHA-256은 16진수 64자리예요 — 입력이 바뀌면 결과도 달라지며 역산은 어려워요",
    ],
    [
      "signature",
      1,
      "전자서명 (Digital Signature)",
      "개인키로 만들고 공개키로 검증하는 거래의 증명으로, 서명 후 내용을 고치면 검증에 실패해요",
    ],
    [
      "ecdsa",
      1,
      "ECDSA",
      "타원곡선을 이용한 전자서명 방식으로, 이 앱은 secp256k1 곡선을 사용해요",
    ],
    [
      "tx",
      2,
      "거래 (Transaction)",
      "누가 누구에게 얼마를 보내는지 담은 기록으로, 송신자 공개키·수신 주소·금액·순번·시각·서명으로 구성돼요",
    ],
    [
      "balance",
      2,
      "잔액 (Balance)",
      "장부에 확정된 지급·송금·보상 기록을 더하고 빼서 계산한 값이에요",
    ],
    [
      "txnonce",
      2,
      "거래 순번 (Transaction Nonce)",
      "지갑이 보낸 거래의 일련번호(0, 1, 2…)로, 같은 거래의 재사용을 막아요 — 채굴용 Nonce와는 다른 값이에요",
    ],
    [
      "doublespend",
      2,
      "이중 지불 (Double Spending)",
      "같은 코인을 두 번 쓰려는 시도로, 이 앱은 대기 거래까지 반영한 잔액·순번 검사로 막아요",
    ],
    [
      "replay",
      2,
      "재전송 공격 (Replay Attack)",
      "이미 처리된 거래를 다시 보내는 공격으로, 거래 순번이 맞지 않으면 거부해요",
    ],
    [
      "faucet",
      2,
      "Faucet (학습용 코인 지급)",
      "지갑당 한 번 50코인을 지급하는 실습 기능으로, 채굴해 블록에 담겨야 잔액에 반영돼요",
    ],
    [
      "mempool",
      3,
      "Mempool (거래 대기열)",
      "검증을 통과했지만 아직 블록에 담기지 않은 거래가 기다리는 곳이에요",
    ],
    [
      "mining",
      3,
      "채굴 (Mining)",
      "대기 거래로 후보 블록을 만들고 작업 증명에 성공해 장부에 올리는 과정으로, 성공한 채굴자에게 보상을 지급해요",
    ],
    [
      "pow",
      3,
      "작업 증명 (Proof-of-Work)",
      "난이도 조건에 맞는 해시를 찾았다는 증거로, 찾으려면 반복 계산이 필요하지만 검증은 해시 한 번이면 돼요",
    ],
    [
      "nonce",
      3,
      "논스 (Nonce, 채굴용)",
      "조건에 맞는 해시를 찾으려고 바꾸는 숫자로, 이 앱은 0부터 1씩 올리며 계산해요",
    ],
    [
      "difficulty",
      3,
      "난이도 (Difficulty)",
      "해시 앞에 필요한 16진수 0의 개수로, 1 높아지면 평균 시도 횟수는 16배예요",
    ],
    [
      "coinbase",
      3,
      "채굴 보상 (Coinbase)",
      "채굴 성공 시 지급하는 10코인으로, 블록의 첫 거래에 기록해요",
    ],
    [
      "block",
      4,
      "블록 (Block)",
      "거래 묶음과 머리말(번호·시각·이전 해시·Merkle Root·난이도·Nonce), 자기 해시를 담은 장부의 한 단위예요",
    ],
    [
      "prevhash",
      4,
      "이전 해시 (Previous Hash)",
      "바로 앞 블록의 해시로, 블록들을 사슬처럼 연결해요",
    ],
    [
      "merkle",
      4,
      "머클 루트 (Merkle Root)",
      "블록의 모든 거래를 하나의 해시로 요약한 값으로, 거래가 바뀌면 값도 달라져요",
    ],
    [
      "genesis",
      4,
      "제네시스 블록 (Genesis Block)",
      "체인의 첫 블록으로, 모든 노드가 같은 블록에서 시작해요",
    ],
    [
      "validation",
      4,
      "체인 검증",
      "모든 블록의 해시·Merkle Root·작업 증명·연결·거래 규칙(서명·잔액·순번)을 다시 확인하는 과정이에요",
    ],
    [
      "propagation",
      5,
      "전파 (Propagation)",
      "거래·블록을 이웃 노드에 전달하는 과정으로, 받은 노드는 각자 검증한 뒤 반영해요",
    ],
    [
      "sync",
      5,
      "동기화 · 누적 작업량",
      "유효한 체인의 누적 작업량을 비교해 장부를 선택해요 — 오프라인 노드는 온라인 복귀 후 직접 동기화해야 해요",
    ],
    [
      "integrity",
      5,
      "무결성 (Integrity)",
      "데이터가 바뀌지 않은 상태로, 이 앱에서는 변조 전후의 검증 결과로 확인해요",
    ],
  ].map(([k, g, t, d]) => ({ k, g, t, d }));
  const MAP = Object.fromEntries(G.map((x) => [x.k, x]));
  window.GLOSSARY = MAP;

  // ---- 툴팁 (마우스 올림 / 키보드 포커스 / 터치 클릭) ----
  const css = document.createElement("style");
  css.textContent = `
  .gl{border-bottom:1.5px dotted var(--accent,#176e60);cursor:help}
  #gtip{position:fixed;z-index:1000;display:none;max-width:min(360px,calc(100vw - 16px));background:#13272e;color:#eaf5ef;border-radius:9px;padding:12px 14px;font:14px/1.8 system-ui,"Malgun Gothic",sans-serif;box-shadow:0 6px 24px #0004;pointer-events:none}
  #gtip b{color:#a2e1c8}
  #dict{border:1px solid var(--line,#dde5e9);border-radius:14px;width:min(760px,94vw);max-height:84vh;padding:0;box-shadow:0 12px 40px #0004;font:16px/1.8 system-ui,"Malgun Gothic",sans-serif;color:var(--ink,#1a2c34);background:var(--surface,#fff)}
  #dict::backdrop{background:rgba(16,22,38,.55)}
  #dict .dh{position:sticky;top:0;background:var(--surface,#fff);padding:18px;border-bottom:1px solid var(--line,#dde5e9);display:flex;gap:10px;align-items:center}
  #dict .dh input{flex:1;min-width:0;padding:9px 12px;border:1px solid var(--line,#dde5e9);color:var(--ink,#1a2c34);background:var(--bg,#f4f6f8);border-radius:8px;font:inherit}
  #dict .dh button{border:1px solid var(--line,#dde5e9);background:var(--surface,#fff);color:var(--accent,#176e60);border-radius:8px;padding:8px 12px;font:inherit;cursor:pointer}
  #dict .db{padding:4px 16px 16px;overflow:auto;max-height:calc(84vh - 66px)}
  #dict h4{margin:20px 0 10px;color:var(--accent,#176e60);font-size:15px;letter-spacing:.4px}
  #dict .de{padding:10px 0;border-bottom:1px solid var(--line,#dde5e9)}#dict .de b{display:block;font-size:17px}#dict .de div{color:var(--muted,#64767e);font-size:16px;margin-top:6px;overflow-wrap:anywhere}`;
  document.head.appendChild(css);
  const tip = document.createElement("div");
  tip.id = "gtip";
  tip.setAttribute("role", "tooltip");
  document.body.appendChild(tip);
  function show(el) {
    const g = MAP[el.dataset.k];
    if (!g) return;
    tip.innerHTML = `<b>${g.t}</b><br>${g.d}`;
    tip.style.display = "block";
    const r = el.getBoundingClientRect(),
      w = tip.offsetWidth,
      h = tip.offsetHeight;
    let x = Math.min(Math.max(8, r.left), window.innerWidth - w - 8);
    let y = r.bottom + 8;
    if (y + h > window.innerHeight - 8) y = Math.max(8, r.top - h - 8);
    tip.style.left = x + "px";
    tip.style.top = y + "px";
  }
  const hide = () => {
    tip.style.display = "none";
  };
  document.addEventListener("mouseover", (e) => {
    const el = e.target.closest && e.target.closest(".gl");
    if (el) show(el);
  });
  document.addEventListener("mouseout", (e) => {
    if (e.target.closest && e.target.closest(".gl")) hide();
  });
  document.addEventListener("focusin", (e) => {
    const el = e.target.closest && e.target.closest(".gl");
    if (el) show(el);
  });
  document.addEventListener("focusout", hide);
  document.addEventListener("click", (e) => {
    const el = e.target.closest && e.target.closest(".gl");
    if (el) show(el);
    else hide();
  });
  window.addEventListener("scroll", hide, true);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") hide();
  });

  // ---- 정적 문구 자동 주석: 각 섹션에서 용어의 첫 등장만 점선 밑줄 ----
  const ALIAS = [
    ["Previous Hash", "prevhash"],
    ["previous_hash", "prevhash"],
    ["Merkle Root", "merkle"],
    ["Proof-of-Work", "pow"],
    ["Blockchain", "blockchain"],
    ["Transaction", "tx"],
    ["Mempool", "mempool"],
    ["Mining", "mining"],
    ["Coinbase", "coinbase"],
    ["coinbase", "coinbase"],
    ["Faucet", "faucet"],
    ["faucet", "faucet"],
    ["Genesis", "genesis"],
    ["Merkle", "merkle"],
    ["Nonce", "nonce"],
    ["nonce", "nonce"],
    ["Wallet", "wallet"],
    ["Block", "block"],
    ["Hash", "hash"],
    ["PoW", "pow"],
    ["Node", "node"],
    ["전자서명", "signature"],
    ["개인키", "private"],
    ["공개키", "public"],
    ["이중 지불", "doublespend"],
    ["재전송", "replay"],
    ["난이도", "difficulty"],
    ["무결성", "integrity"],
    ["동기화", "sync"],
    ["전파", "propagation"],
    ["잔액", "balance"],
    ["해시", "hash"],
    ["블록체인", "blockchain"],
    ["채굴", "mining"],
    ["노드", "node"],
    ["지갑", "wallet"],
    ["주소", "address"],
    ["블록", "block"],
  ].sort((a, b) => b[0].length - a[0].length);
  const LOOK = Object.fromEntries(ALIAS);
  const RX = new RegExp(
    ALIAS.map((a) => a[0].replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|"),
    "g",
  );
  function annotateEl(el, seen) {
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, {
      acceptNode: (n) => {
        if (
          n.parentElement.closest(
            ".gl,script,style,input,select,option,textarea,button,pre,video",
          )
        )
          return NodeFilter.FILTER_REJECT;
        RX.lastIndex = 0;
        return RX.test(n.nodeValue)
          ? NodeFilter.FILTER_ACCEPT
          : NodeFilter.FILTER_REJECT;
      },
    });
    RX.lastIndex = 0;
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    nodes.forEach((n) => {
      const text = n.nodeValue,
        frag = document.createDocumentFragment();
      let last = 0,
        changed = false;
      RX.lastIndex = 0;
      for (let m; (m = RX.exec(text)); ) {
        const k = LOOK[m[0]];
        if (seen.has(k)) continue;
        seen.add(k);
        changed = true;
        frag.appendChild(document.createTextNode(text.slice(last, m.index)));
        const s = document.createElement("span");
        s.className = "gl";
        s.tabIndex = 0;
        s.dataset.k = k;
        s.textContent = m[0];
        frag.appendChild(s);
        last = m.index + m[0].length;
      }
      if (changed) {
        frag.appendChild(document.createTextNode(text.slice(last)));
        n.parentNode.replaceChild(frag, n);
      }
    });
  }
  window.annotateGlossary = function (root) {
    (root || document).querySelectorAll("section").forEach((sec) => {
      const seen = new Set();
      sec.querySelectorAll("h2, .desc").forEach((el) => annotateEl(el, seen));
      sec.querySelectorAll("table").forEach((t) => {
        const s2 = new Set();
        t.querySelectorAll("th").forEach((th) => annotateEl(th, s2));
      });
    });
  };

  // ---- 용어 사전 (검색 가능) ----
  let dlg;
  window.openDict = function () {
    if (!dlg) {
      dlg = document.createElement("dialog");
      dlg.id = "dict";
      dlg.setAttribute("aria-label", "블록체인 용어 사전");
      dlg.innerHTML = `<div class="dh"><input id="dq" aria-label="용어 검색" placeholder="용어 검색 · 해시, 논스 등"><button id="dx">닫기</button></div><div class="db" id="dbody"></div>`;
      document.body.appendChild(dlg);
      const render = (q) => {
        q = (q || "").trim().toLowerCase();
        $b.innerHTML =
          GROUPS.map((gn, gi) => {
            const items = G.filter(
              (x) =>
                x.g === gi &&
                (!q || (x.t + x.d + x.k).toLowerCase().includes(q)),
            );
            return items.length
              ? `<h4>${gn}</h4>` +
                  items
                    .map(
                      (x) =>
                        `<div class="de"><b>${x.t}</b><div>${x.d}</div></div>`,
                    )
                    .join("")
              : "";
          }).join("") ||
          '<p style="color:var(--muted,#64767e)">검색 결과가 없어요</p>';
      };
      const $b = dlg.querySelector("#dbody");
      dlg
        .querySelector("#dq")
        .addEventListener("input", (e) => render(e.target.value));
      dlg.querySelector("#dx").addEventListener("click", () => dlg.close());
      dlg.addEventListener("click", (e) => {
        if (e.target === dlg) dlg.close();
      });
      render("");
    }
    dlg.showModal();
    dlg.querySelector("#dq").focus();
  };

  if (document.readyState === "loading")
    document.addEventListener("DOMContentLoaded", () =>
      window.annotateGlossary(),
    );
  else window.annotateGlossary();
})();
