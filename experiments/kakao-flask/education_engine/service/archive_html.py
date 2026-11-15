"""Offline, script-free HTML results, with no recovery secrets."""

import html
import json

from ..core.wire import to_wire
from .checkpoint import decode


def esc(value):
    return html.escape(str(value))


def detail(label, value):
    text = esc(json.dumps(value, ensure_ascii=False, indent=2))
    return f"<details><summary>{esc(label)}</summary><pre>{text}</pre></details>"


def render(item):
    snapshot = item["snapshot"]
    lab = snapshot.get("lab", snapshot)
    engine = decode(item["checkpoint"]["session"]["lab"])
    names = {w["address"]: w["name"] for w in lab.get("wallets", [])}
    names.update({w["address"]: w["name"] for w in lab.get("minerWallets", {}).values()})
    sections = []
    all_transactions = {}
    if engine:
        for node in engine.nodes.values():
            for block in node.blocks.values():
                for tx in block.transactions:
                    all_transactions[tx.id] = tx
            for tx in node.mempool:
                all_transactions[tx.id] = tx
    for node_id, node in lab.get("nodes", {}).items():
        rows = "".join(
            f"<tr><td>{esc(names.get(address, address))}</td>"
            f"<td>{b['confirmed']}</td><td>{b['spendable']}</td><td>{b['immature']}</td></tr>"
            for address, b in node["balances"].items()
        )
        sections.append(
            f"<section><h2>노드 {esc(node_id)}</h2>"
            f"<p>높이 {len(node['chain']) - 1} · 멤풀 {len(node['mempool'])}건 · "
            f"누적 작업량 {esc(node.get('chainWork', ''))}</p>"
            "<table><caption>현재 지갑 상태</caption><tr><th>지갑</th>"
            f"<th>미소비 합계</th><th>사용 가능</th><th>미성숙 보상</th></tr>{rows}</table>"
            + detail("활성 블록·거래 상세", node["chain"])
            + detail("UTXO 상세", node["utxos"])
            + detail("멤풀", node["mempool"])
            + detail(
                "분기와 보류 블록",
                {
                    "branches": node.get("branches", []),
                    "blocks": to_wire(engine.nodes[node_id].blocks),
                    "pendingBlocks": to_wire(engine.nodes[node_id].pending_blocks),
                },
            )
            + "</section>"
        )
    transfers = []
    for tx in all_transactions.values():
        if tx.kind != "regular":
            continue
        inputs = []
        total = 0
        for tx_input in tx.inputs:
            previous = all_transactions.get(tx_input.tx_id)
            output = previous.outputs[tx_input.output_index] if previous else None
            amount = output.amount if output else None
            total += amount or 0
            inputs.append(
                {"TXID": tx_input.tx_id, "출력 번호": tx_input.output_index, "금액": amount}
            )
        fee = total - sum(o.amount for o in tx.outputs)
        outputs = " + ".join(
            f"{esc(names.get(o.address, o.address))} {o.amount}" for o in tx.outputs
        )
        locations = []
        for node_id, node in engine.nodes.items():
            included = any(tx in b.transactions for b in node.chain)
            status = "블록 포함" if included else "멤풀 대기" if tx in node.mempool else "미포함"
            locations.append(f"{node_id}: {status}")
        transfers.append(
            f"<article><h3>거래 {esc(tx.id[:12])}…</h3>"
            f"<p>입력 {total} → {outputs} + 수수료 {fee}</p><p>{' · '.join(locations)}</p>"
            + detail("소비할 출력의 식별자·금액", inputs)
            + detail("전체 거래·서명", to_wire(tx))
            + "</article>"
        )
    events = []
    for entry in item["events"]:
        event = entry.get("event", {})
        actor = event.get("actor", event.get("node", ""))
        events.append(
            f"<li><time>{esc(entry['at'])}</time> · {esc(actor)} · "
            f"{esc(event.get('message', ''))}"
            + (detail("실험·거래 상세", event["detail"]) if "detail" in event else "")
            + "</li>"
        )
    mode = "공동 수업" if item["mode"] == "classroom" else "개인 실습"
    body = (
        detail(
            "초기 배정·실습 설정",
            {"difficulty": lab.get("difficulty"), "genesis": lab.get("genesis")},
        )
        + "".join(sections)
        + "<section><h2>거래 입력 → 출력·수수료</h2>"
        + ("".join(transfers) or "일반 거래 없음")
        + "</section>"
        + detail("전파 대기 메시지", to_wire(engine.queue) if engine else [])
        + detail("채굴 결과", lab.get("race") or lab.get("mining"))
    )
    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy"
 content="default-src 'none'; style-src 'unsafe-inline'">
<title>{esc(item["title"])} · 실습 결과</title><style>
:root{{color-scheme:light dark}}
body{{font:16px/1.7 system-ui,sans-serif;max-width:960px;margin:auto;
 padding:24px;overflow-wrap:anywhere}}
section,details{{border:1px solid #8886;border-radius:12px;padding:16px;margin:16px 0}}
summary{{cursor:pointer;font-weight:bold}}pre{{white-space:pre-wrap;font-size:13px}}
table{{width:100%;border-collapse:collapse}}td,th{{text-align:left;
 border-bottom:1px solid #8886;padding:8px}}time{{font-size:.85em}}li{{margin-bottom:12px}}
</style></head><body><h1>{esc(item["title"])}</h1>
<p>{mode} · 저장 시각 {esc(item["updated"])}</p>
<p>서버 없이 열어보는 실습 결과입니다. 이어 하기는 앱의 저장된 실습에서 가능합니다.</p>
<p>실제 자산이 아닌 교육용 기록입니다. 보조금 50과 일반 거래 수수료는 구분하며,
 채굴 보상은 코인베이스 출력이 100블록 성숙한 뒤 사용할 수 있습니다.</p>
{body}<section><h2>활동 기록</h2><ol>{"".join(events)}</ol></section></body></html>"""
