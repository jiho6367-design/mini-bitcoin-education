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
      "거래 기록 묶음(블록)을 시간 순서로 사슬처럼 이어 붙인 장부. 앞 블록의 지문을 뒤 블록이 품고 있어, 중간을 몰래 고치면 이후가 전부 어긋납니다.",
    ],
    [
      "node",
      0,
      "노드 (Node)",
      "네트워크에 참여하는 컴퓨터 한 대. 각자 자기 장부와 대기열을 갖고 서로 검증합니다. 비유: 같은 장부 사본을 가진 은행 지점. (이 시스템에서는 한 프로그램 안의 가상 노드 3개)",
    ],
    [
      "wallet",
      1,
      "지갑 (Wallet)",
      "코인이 든 주머니가 아니라 ‘나를 증명하는 열쇠 꾸러미’(개인키·공개키·주소). 잔액은 장부 기록으로 계산됩니다.",
    ],
    [
      "private",
      1,
      "개인키 (Private Key)",
      "서명할 때 쓰는 비밀 정보입니다. 이 교육용 앱은 서버가 키를 보관하며, 사용자가 키 보기를 선택했을 때만 표시합니다. 실제 자산에 사용하지 마세요.",
    ],
    [
      "public",
      1,
      "공개키 (Public Key)",
      "개인키와 짝을 이루는 키. 남들이 서명이 진짜인지 확인하는 데 씁니다. 공개해도 개인키를 알아낼 수 없습니다.",
    ],
    [
      "address",
      1,
      "주소 (Address)",
      "코인을 받을 때 알려 주는 계좌번호. 이 시스템에서는 공개키를 SHA-256으로 해시한 값입니다(실제 비트코인은 더 복잡한 변환 사용).",
    ],
    [
      "hash",
      1,
      "해시 (Hash)",
      "데이터를 넣으면 항상 같은 길이의 ‘지문’을 만드는 계산(SHA-256, 64자리). 한 글자만 달라져도 전혀 다른 지문이 나오고, 지문으로 원래 데이터를 되돌릴 수 없습니다.",
    ],
    [
      "signature",
      1,
      "전자서명 (Digital Signature)",
      "개인키로 거래서에 찍는 전자 도장. ‘주인이 만들었다’와 ‘내용이 안 바뀌었다’를 함께 증명하며, 한 글자만 바뀌어도 검증이 실패합니다.",
    ],
    [
      "ecdsa",
      1,
      "ECDSA",
      "키 생성과 서명·검증에 쓰는 수학 방식(타원곡선 전자서명). 비트코인과 같은 secp256k1 곡선을 씁니다.",
    ],
    [
      "tx",
      2,
      "거래 (Transaction)",
      "‘누가 누구에게 얼마를 보낸다’는 전표. 송신자 공개키, 수신 주소, 금액, 거래 순번, 시각, 서명을 담습니다.",
    ],
    [
      "balance",
      2,
      "잔액 (Balance)",
      "지갑에 저장된 숫자가 아니라, 장부의 모든 거래를 처음부터 더하고 빼서 계산한 값입니다.",
    ],
    [
      "txnonce",
      2,
      "거래 순번 (Transaction Nonce)",
      "한 지갑이 보낸 거래의 일련번호(0, 1, 2…). 같은 거래를 복사해 다시 내는 것을 막습니다. 채굴용 Nonce와는 다른 값입니다.",
    ],
    [
      "doublespend",
      2,
      "이중 지불 (Double Spending)",
      "같은 코인을 두 번 쓰려는 시도. 대기 중인 거래까지 반영해 잔액을 계산하므로 막힙니다.",
    ],
    [
      "replay",
      2,
      "재전송 공격 (Replay Attack)",
      "이미 처리된 거래를 복사해 다시 보내는 공격. 거래 순번이 맞지 않으면 거부됩니다.",
    ],
    [
      "faucet",
      2,
      "Faucet (학습용 코인 지급)",
      "실습을 위해 새 지갑에 코인(50)을 나눠 주는 기능. 지갑당 1회이며, 채굴해서 블록에 담겨야 잔액에 반영됩니다.",
    ],
    [
      "mempool",
      3,
      "Mempool (거래 대기열)",
      "검증은 통과했지만 아직 블록에 담기지 않은 거래들이 기다리는 곳. 비유: 은행 창구의 대기 번호표 줄.",
    ],
    [
      "mining",
      3,
      "채굴 (Mining)",
      "대기 거래를 모아 새 블록을 만들고, 퍼즐을 풀어 장부에 올리는 일. 성공하면 보상 코인을 받습니다.",
    ],
    [
      "pow",
      3,
      "작업 증명 (Proof-of-Work)",
      "‘이만큼 계산했다’는 증거. 정답 찾기는 어렵지만 확인은 해시 한 번이면 되어서, 과거 기록을 조작하려면 막대한 계산을 다시 해야 합니다. 비유: 자물쇠 번호 대입.",
    ],
    [
      "nonce",
      3,
      "논스 (Nonce, 채굴용)",
      "퍼즐을 풀려고 계속 바꿔 보는 숫자. 0부터 1씩 올리며 해시를 계산합니다. 비유: 자물쇠에 넣어 보는 번호.",
    ],
    [
      "difficulty",
      3,
      "난이도 (Difficulty)",
      "해시가 몇 개의 0으로 시작해야 하는지 정한 값. 1 올라갈 때마다 평균 시도 횟수가 약 16배가 됩니다.",
    ],
    [
      "coinbase",
      3,
      "채굴 보상 (Coinbase)",
      "채굴에 성공한 사람에게 새로 지급하는 코인(10). 블록의 첫 거래로 기록됩니다.",
    ],
    [
      "block",
      4,
      "블록 (Block)",
      "거래 묶음 + 머리말(번호·시각·이전 해시·Merkle Root·난이도·Nonce) + 자기 해시. 비유: 장부의 한 페이지.",
    ],
    [
      "prevhash",
      4,
      "이전 해시 (Previous Hash)",
      "바로 앞 블록의 지문. 블록을 사슬로 잇는 고리라서, 중간 블록을 고치면 연결이 끊어집니다.",
    ],
    [
      "merkle",
      4,
      "머클 루트 (Merkle Root)",
      "블록 안 모든 거래를 하나의 지문으로 요약한 값. 거래가 하나라도 바뀌면 값이 달라집니다.",
    ],
    [
      "genesis",
      4,
      "제네시스 블록 (Genesis Block)",
      "체인의 첫 블록. 앞선 블록이 없고, 모든 노드가 똑같은 것으로 시작합니다.",
    ],
    [
      "validation",
      4,
      "체인 검증",
      "모든 블록의 해시 · Merkle Root · 작업 증명 · 연결 · 거래(서명·잔액) 5가지를 처음부터 끝까지 다시 확인하는 과정.",
    ],
    [
      "propagation",
      5,
      "전파 (Propagation)",
      "거래나 블록을 받은 노드가 이웃 노드에 전달하는 것. 받은 노드는 스스로 검증한 뒤에만 반영합니다.",
    ],
    [
      "sync",
      5,
      "동기화 · 누적 작업량",
      "서로 다른 장부 중 검증을 통과한 체인의 누적 작업량을 비교해 선택하는 과정입니다. 단순히 블록 수가 많은 체인을 고르는 것이 아닙니다. 이 앱의 오프라인 노드는 온라인으로 돌아온 뒤 직접 동기화합니다.",
    ],
    [
      "integrity",
      5,
      "무결성 (Integrity)",
      "데이터가 몰래 바뀌지 않았음이 보장되는 성질. 블록체인의 핵심 가치이며, 변조 실험으로 확인합니다.",
    ],
  ].map(([k, g, t, d]) => ({ k, g, t, d }));
  const MAP = Object.fromEntries(G.map((x) => [x.k, x]));
  window.GLOSSARY = MAP;

  // ---- 툴팁 (마우스 올림 / 키보드 포커스 / 터치 클릭) ----
  const css = document.createElement("style");
  css.textContent = `
  .gl{border-bottom:1.5px dotted var(--accent,#176e60);cursor:help}
  #gtip{position:fixed;z-index:1000;display:none;max-width:320px;background:#13272e;color:#eaf5ef;border-radius:9px;padding:12px 14px;font:12px/1.7 system-ui,"Malgun Gothic",sans-serif;box-shadow:0 6px 24px #0004;pointer-events:none}
  #gtip b{color:#a2e1c8}
  #dict{border:1px solid var(--line,#dde5e9);border-radius:14px;width:min(760px,94vw);max-height:84vh;padding:0;box-shadow:0 12px 40px #0004;font:14px/1.65 system-ui,"Malgun Gothic",sans-serif;color:var(--ink,#1a2c34);background:var(--surface,#fff)}
  #dict::backdrop{background:rgba(16,22,38,.55)}
  #dict .dh{position:sticky;top:0;background:var(--surface,#fff);padding:18px;border-bottom:1px solid var(--line,#dde5e9);display:flex;gap:10px;align-items:center}
  #dict .dh input{flex:1;min-width:0;padding:9px 12px;border:1px solid var(--line,#dde5e9);color:var(--ink,#1a2c34);background:var(--bg,#f4f6f8);border-radius:8px;font:inherit}
  #dict .dh button{border:1px solid var(--line,#dde5e9);background:var(--surface,#fff);color:var(--accent,#176e60);border-radius:8px;padding:8px 12px;font:inherit;cursor:pointer}
  #dict .db{padding:4px 16px 16px;overflow:auto;max-height:calc(84vh - 66px)}
  #dict h4{margin:20px 0 10px;color:var(--accent,#176e60);font-size:12px;letter-spacing:.4px}
  #dict .de{padding:10px 0;border-bottom:1px solid var(--line,#dde5e9)}#dict .de b{display:block;font-size:13px}#dict .de div{color:var(--muted,#64767e);font-size:12px;margin-top:4px}`;
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
      dlg.innerHTML = `<div class="dh"><input id="dq" aria-label="용어 검색" placeholder="용어 검색 (예: 해시, 논스, Mempool)"><button id="dx">닫기</button></div><div class="db" id="dbody"></div>`;
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
          '<p style="color:var(--muted,#64767e)">검색 결과가 없습니다.</p>';
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
