"""교육용 블록체인 데모 (GUI).   실행:  python demo.py

지갑 이름을 직접 입력해 원하는 만큼 만들고, 보내는 사람/받는 사람/채굴자를 골라서 쓴다.
흐름: 지갑 생성 -> 코인 지급 -> 블록 채굴 -> 거래 전송 -> 블록 채굴 -> 체인 검증 -> 변조 실험 -> 난이도 실험
결과는 모두 창 안의 텍스트 박스에 표시된다.
"""
import threading
import tkinter as tk
from tkinter import scrolledtext, ttk

from blockchain import FAUCET_AMOUNT, Blockchain
from mempool import Mempool
from miner import mine
from transaction import FAUCET, TRANSFER, Transaction
from wallet import Wallet

BLOCK_SHOW_EVERY = 500         # 블록 채굴 중 해시를 몇 번마다 보여줄지

win = None                     # GUI 창 (main()에서 생성). None이면 터미널로 출력(테스트용)
chain = pool = None
wallets = {}                   # {이름: Wallet}


# ---------------------------------------------------------------------
# 출력 / 상태 관리
# ---------------------------------------------------------------------
def log(text):
    if win is None:
        print(text, end='')
        return
    # tkinter는 메인 스레드에서만 화면을 바꿀 수 있어서 after 사용
    win.after(0, lambda: (results_box.insert(tk.END, text), results_box.see(tk.END)))


def reset(difficulty=3):
    global chain, pool, wallets
    chain = Blockchain(difficulty=difficulty)
    pool = Mempool(chain)
    wallets = {}


def balances_text():
    st = chain.state()
    return "잔액: " + ", ".join(f"{n} {st.balance(w.address)}" for n, w in wallets.items())


# ---------------------------------------------------------------------
# 기능들 (모두 log()로 결과를 출력)
# ---------------------------------------------------------------------
def act_create_wallet(name):
    name = name.strip()
    if not name:
        log("지갑 이름을 입력하세요.\n\n")
        return
    if name in wallets:
        log(f"'{name}' 지갑은 이미 있습니다. 다른 이름을 입력하세요.\n\n")
        return
    w = wallets[name] = Wallet(name)
    log(f"[지갑 생성] {name}\n  주소   : {w.address[:24]}...\n  공개키 : {w.public_key[:24]}...\n"
        f"  개인키 : {w.private_key[:8]}... (비공개)\n"
        f"현재 지갑 {len(wallets)}개: {', '.join(wallets)}\n\n")


def act_faucet():
    if not wallets:
        log("지갑이 없습니다. 먼저 지갑을 만드세요.\n\n")
        return
    log("[학습용 코인 지급(faucet) -> Mempool 등록]  (지갑당 1회)\n")
    for n, w in wallets.items():
        ok, why = pool.add(Transaction(FAUCET, "", w.address, FAUCET_AMOUNT))
        log(f"{n}: {'등록됨' if ok else '거부됨'} ({why})\n")
    log(f"Mempool 대기 거래 수: {len(pool.txs)}  (블록에 담기기 전이라 잔액은 그대로)\n"
        f"{balances_text()}\n\n")


def act_send(sender, receiver, amount):
    if sender not in wallets or receiver not in wallets:
        log("보내는 사람과 받는 사람을 선택하세요. (지갑을 먼저 만드세요)\n\n")
        return
    if sender == receiver:
        log("보내는 사람과 받는 사람이 같습니다.\n\n")
        return
    s, r = wallets[sender], wallets[receiver]
    log("[거래 생성 -> 전자서명 -> 검증 -> Mempool]\n")
    nonce = pool.pending_state().nonces.get(s.address, 0)
    tx = Transaction(TRANSFER, s.public_key, r.address, amount, nonce=nonce)
    log(f"{sender} -> {receiver}  {amount}코인\n서명 전 검증 : {tx.basic_check()}\n")
    tx.sign(s)
    log(f"서명 후 검증 : {tx.basic_check()}\n")
    ok, why = pool.add(tx)
    log(f"Mempool 등록 : {'성공' if ok else '실패'} ({why})\n")
    bad = tx.copy()
    bad.amount = amount + 1000
    log(f"금액 변조({amount}->{amount + 1000}) : {pool.add(bad)}\n")
    over = Transaction(TRANSFER, s.public_key, r.address, 9999,
                       nonce=pool.pending_state().nonces.get(s.address, 0))
    over.sign(s)
    log(f"잔액 초과({sender} 9999) : {pool.add(over)}\n\n")


def act_mine(difficulty, miner_name):
    if miner_name not in wallets:
        log("채굴자를 선택하세요. (지갑을 먼저 만드세요)\n\n")
        return
    chain.difficulty = difficulty
    log(f"+++ Start  (블록 #{chain.tip.index + 1}, difficulty={difficulty}, "
        f"채굴자={miner_name}, 대기 거래 {len(pool.txs)}개)\n")

    def on_try(nonce, h):
        log(f"nonce={nonce:<8} hash={h[:40]}...\n")

    block, attempts, sec = mine(chain, pool, wallets[miner_name].address,
                                on_try=on_try, show_every=BLOCK_SHOW_EVERY)
    log(f"+++ Bingo\n블록 #{block.index}\nHASH  : {block.hash}\nNONCE  : {block.nonce}\n"
        f"시도 {attempts:,}회\nrunning time : {sec}\n"
        f"previous_hash : {block.previous_hash[:24]}...\n"
        f"merkle_root   : {block.merkle_root[:24]}...  (거래 {len(block.transactions)}개)\n"
        f"{balances_text()}\n\n")


def validate_text(c):
    ok, results = c.validate_chain()
    lines = [f"체인 전체 유효 여부: {'정상' if ok else '비정상'}\n"]
    for r in results:
        mark = "정상" if r["valid"] else "깨짐"
        lines.append(f"  블록 #{r['index']}: {mark}\n")
        for why in r["reasons"]:
            lines.append(f"      - {why}\n")
    return "".join(lines)


def act_validate():
    log("[체인 검증: 해시 / Merkle / PoW / 연결 / 거래]\n" + validate_text(chain) + "\n")


def clone_chain(c):
    return Blockchain([b.copy() for b in c.chain], c.difficulty)


def act_tamper():
    target = next((i for i, b in enumerate(chain.chain) if i > 0 and len(b.transactions) > 1), None)
    if target is None:
        log("변조할 거래가 있는 블록이 없습니다. 지갑 -> 코인 지급 -> 블록 채굴을 먼저 하세요.\n\n")
        return
    log(f"[변조 실험: 블록 #{target}의 거래 금액을 999로 변경] (복사본에서 실험)\n")
    forged = clone_chain(chain)
    blk = forged.chain[target]
    blk.transactions[1].amount = 999
    log("1) 내용만 바꿨을 때\n" + validate_text(forged))

    log("\n2) Merkle Root를 맞추고 PoW를 다시 수행했을 때\n")
    blk.merkle_root = blk.calculate_merkle_root()
    att = blk.mine_to_target()
    log(f"재채굴: 시도 {att:,}회, 새 hash={blk.hash[:20]}...\n" + validate_text(forged))
    log("=> 한 블록을 고치면 그 블록부터 이후 모든 블록을 다시 채굴해야 한다.\n\n")


def act_difficulty(max_d):
    log(f"[Difficulty 실험] 난이도 1~{max_d} (오를수록 시도 횟수가 평균 16배씩 늘어난다)\n")
    tmp_miner = Wallet("tmp")
    for d in range(1, max_d + 1):
        c2 = Blockchain(difficulty=d)
        _, att, sec = mine(c2, Mempool(c2), tmp_miner.address)
        log(f"난이도 {d}: 시도 {att:>9,}회   {sec:.3f}초\n")
    log("\n")


# ---------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------
def main():
    global win, results_box
    reset()
    win = tk.Tk()
    win.title("교육용 블록체인 데모")
    win.geometry("700x620")

    pad = dict(padx=4, pady=3)

    # --- 행 0: 난이도 ---
    ttk.Label(win, text="Difficulty :").grid(column=0, row=0, sticky=tk.E, **pad)
    diff_entry = ttk.Entry(win, width=8)
    diff_entry.insert(0, "3")
    diff_entry.grid(column=1, row=0, sticky=tk.W, **pad)
    ttk.Label(win, text="(1~5, 해시 앞자리 0의 개수)").grid(column=2, row=0, columnspan=3, sticky=tk.W)

    # --- 행 1: 지갑 이름 / 생성 ---
    ttk.Label(win, text="지갑 이름 :").grid(column=0, row=1, sticky=tk.E, **pad)
    name_entry = ttk.Entry(win, width=16)
    name_entry.grid(column=1, row=1, sticky=tk.W, **pad)

    # --- 행 2: 채굴자 ---
    ttk.Label(win, text="채굴자 :").grid(column=0, row=2, sticky=tk.E, **pad)
    miner_combo = ttk.Combobox(win, width=14, state="readonly")
    miner_combo.grid(column=1, row=2, sticky=tk.W, **pad)

    # --- 행 3: 거래 ---
    ttk.Label(win, text="보내는 사람 :").grid(column=0, row=3, sticky=tk.E, **pad)
    sender_combo = ttk.Combobox(win, width=14, state="readonly")
    sender_combo.grid(column=1, row=3, sticky=tk.W, **pad)
    ttk.Label(win, text="받는 사람 :").grid(column=2, row=3, sticky=tk.E, **pad)
    receiver_combo = ttk.Combobox(win, width=14, state="readonly")
    receiver_combo.grid(column=3, row=3, sticky=tk.W, **pad)
    ttk.Label(win, text="금액 :").grid(column=4, row=3, sticky=tk.E, **pad)
    amount_entry = ttk.Entry(win, width=8)
    amount_entry.insert(0, "20")
    amount_entry.grid(column=5, row=3, sticky=tk.W, **pad)

    buttons = []

    def refresh_combos():
        names = list(wallets)
        for combo, default_idx in ((miner_combo, 0), (sender_combo, 0), (receiver_combo, 1)):
            combo["values"] = names
            if combo.get() not in names:
                combo.set(names[min(default_idx, len(names) - 1)] if names else "")

    def set_buttons(state):
        for b in buttons:
            b.config(state=state)

    def run(fn, *args):
        set_buttons("disabled")

        def work():
            try:
                fn(*args)
            except Exception as e:
                log(f"오류: {e}\n\n")
            finally:
                win.after(0, lambda: (set_buttons("normal"), refresh_combos()))

        threading.Thread(target=work, daemon=True).start()

    def read_int(entry, label, lo, hi):
        try:
            v = int(entry.get())
        except ValueError:
            log(f"{label}에는 숫자를 입력하세요.\n")
            return None
        if not lo <= v <= hi:
            log(f"{label}는 {lo}~{hi} 사이로 입력하세요.\n")
            return None
        return v

    def click_create():
        run(act_create_wallet, name_entry.get())
        name_entry.delete(0, tk.END)

    def click_send():
        amt = read_int(amount_entry, "금액", 1, 10 ** 9)
        if amt is not None:
            run(act_send, sender_combo.get(), receiver_combo.get(), amt)

    def click_mine():
        d = read_int(diff_entry, "Difficulty", 1, 5)      # 5는 오래 걸릴 수 있음
        if d is not None:
            run(act_mine, d, miner_combo.get())

    def click_difficulty():
        d = read_int(diff_entry, "Difficulty", 1, 5)
        if d is not None:
            run(act_difficulty, d)

    def click_reset():
        reset()
        results_box.delete("1.0", tk.END)
        refresh_combos()
        log("초기화했습니다. 새 체인(Genesis)으로 시작합니다.\n\n")

    create_btn = ttk.Button(win, text="1 지갑 생성", command=click_create)
    create_btn.grid(column=2, row=1, sticky=tk.W, **pad)
    buttons.append(create_btn)
    name_entry.bind("<Return>", lambda e: click_create())

    bar = ttk.Frame(win)
    bar.grid(column=0, row=4, columnspan=6, pady=4)
    items = [("2 코인 지급", lambda: run(act_faucet)),
             ("3 거래 전송", click_send),
             ("4 블록 채굴", click_mine),
             ("5 체인 검증", lambda: run(act_validate)),
             ("6 변조 실험", lambda: run(act_tamper)),
             ("7 난이도 실험", click_difficulty),
             ("초기화", click_reset)]
    for i, (label, cmd) in enumerate(items):
        b = ttk.Button(bar, text=label, command=cmd, width=12)
        b.grid(column=i % 4, row=i // 4, padx=2, pady=2)
        buttons.append(b)

    results_box = scrolledtext.ScrolledText(win, width=84, height=24, font=("Consolas", 10))
    results_box.grid(column=0, row=5, columnspan=6, padx=6, pady=4)
    log("순서: 지갑 이름 입력 후 '1 지갑 생성'(여러 개) -> 2 코인 지급 -> 4 블록 채굴 -> "
        "3 거래 전송 -> 4 블록 채굴 -> 5 체인 검증 -> 6 변조 실험\n"
        "(거래는 코인 지급이 블록에 담긴 뒤에 보낼 수 있어요. 난이도는 처음엔 3 추천)\n\n")

    name_entry.focus()
    win.mainloop()


if __name__ == "__main__":
    main()