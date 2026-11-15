import tkinter as tk
from tkinter import ttk
from tkinter import scrolledtext
import threading

from blockchain import Blockchain, FAUCET_AMOUNT
from mempool import Mempool
from miner import mine
from transaction import Transaction, FAUCET
from wallet import Wallet

SHOW_EVERY = 500   # 해시를 몇 번마다 화면에 보여줄지

chain = Blockchain()
pool = Mempool(chain)
miner_wallet = Wallet("Miner")


def log(text):
    # tkinter는 메인 스레드에서만 화면을 바꿀 수 있어서 after 사용
    win.after(0, lambda: (results_entered.insert(tk.END, text),
                          results_entered.see(tk.END)))


def click_start():
    msg = message_entered.get()
    try:
        difficulty = int(bits_entered.get())
    except ValueError:
        log("Difficulty에는 숫자를 입력하세요.\n")
        return
    if not 1 <= difficulty <= 5:
        log("Difficulty는 1~5 사이로 입력하세요.\n")
        return

    b1.config(state="disabled")
    threading.Thread(target=run_mining, args=(msg, difficulty), daemon=True).start()


def run_mining(msg, difficulty):
    chain.difficulty = difficulty

    # 블록에 담을 거래 하나 만들기 (새 지갑에 코인 지급)
    wallet = Wallet(msg)
    pool.add(Transaction(FAUCET, "", wallet.address, FAUCET_AMOUNT))

    log(f'+++ Start (Block #{chain.tip.index + 1}, difficulty={difficulty})\n')

    def on_try(nonce, h):
        log(f'nonce={nonce:<8} hash={h[:30]}...\n')

    block, attempts, sec = mine(chain, pool, miner_wallet.address,
                                on_try=on_try, show_every=SHOW_EVERY)

    log('+++ Bingo\n'
        f'---> HASH  : {block.hash}\n'
        f'---> NONCE : {block.nonce}\n'
        f'---> 시도 횟수 : {attempts:,}회\n'
        f'---> running time : {sec:.4f} s\n'
        f'---> 현재 체인 길이 : {len(chain.chain)}  (유효: {chain.is_valid()})\n\n')

    win.after(0, lambda: b1.config(state="normal"))


win = tk.Tk()
win.title("MINING - 교육용 블록체인")
win.geometry("520x380")

ttk.Label(win, text="Message : ").grid(column=0, row=0, sticky=tk.E)
message = tk.StringVar(value='Pyeontaek University!')
message_entered = ttk.Entry(win, width=24, textvariable=message)
message_entered.grid(column=1, row=0)

ttk.Label(win, text="Difficulty : ").grid(column=0, row=1, sticky=tk.E)
bits = tk.StringVar(value='3')
bits_entered = ttk.Entry(win, width=24, textvariable=bits)
bits_entered.grid(column=1, row=1)

b1 = ttk.Button(win, text="Start mining!!", command=click_start)
b1.grid(column=3, row=0)

results_entered = scrolledtext.ScrolledText(win, width=62, height=18)
results_entered.grid(column=0, row=2, columnspan=4)

message_entered.focus()
win.mainloop()