import type { ReactNode } from "react";
import type { ValidationResult } from "../core/types";

export const short = (value: string, size = 12) =>
  value
    ? `${value.slice(0, size)}…${value.slice(-6)}`
    : "아직 계산하지 않았습니다";
export function Code({ children }: { children: ReactNode }) {
  return <code className="hash">{children}</code>;
}
export function Notice({ children }: { children: ReactNode }) {
  return (
    <div className="notice">
      <span aria-hidden="true">ⓘ</span>
      <div>{children}</div>
    </div>
  );
}
export function Panel({
  title,
  eyebrow,
  children,
  className = "",
}: {
  title?: string;
  eyebrow?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`panel ${className}`}>
      {eyebrow && <div className="eyebrow">{eyebrow}</div>}
      {title && <h2>{title}</h2>}
      {children}
    </section>
  );
}
export function Validation({ value }: { value: ValidationResult | null }) {
  if (!value)
    return (
      <div className="empty">
        거래를 서명한 다음 검증을 실행해 보세요.
        <small>각 노드가 같은 규칙으로 독립적으로 검사합니다.</small>
      </div>
    );
  return (
    <div className="validation" aria-live="polite">
      <div className={`result ${value.valid ? "good" : "bad"}`}>
        <strong>
          {value.valid ? "✓ 검증 통과" : "✕ 검증 실패"}{" "}
          <small>{value.code}</small>
        </strong>
        <p>{value.message}</p>
      </div>
      <ol className="checks">
        {value.steps.map((s, i) => (
          <li key={i} className={s.ok ? "good-text" : "bad-text"}>
            <span>{s.ok ? "✓" : "✕"}</span>
            <div>
              <strong>{s.label}</strong>
              <small>{s.message}</small>
            </div>
          </li>
        ))}
      </ol>
    </div>
  );
}
export const differences = [
  ["코인 발행", "Genesis에서 100 교육용 코인 배정. 채굴 보상·반감기 없음."],
  [
    "거래",
    "UTXO·거스름돈 유지. 한 지갑의 입력만 사용하며 수수료는 0. Script·SegWit·Taproot 제외.",
  ],
  [
    "키·주소",
    "실제 secp256k1 ECDSA 서명. mini_ 주소는 Bitcoin 주소가 아님. 복구 구문 없음.",
  ],
  [
    "해시·인코딩",
    "고정 순서 JSON과 단일 SHA-256. Bitcoin의 바이너리 직렬화·이중 해시와 다름.",
  ],
  ["거래 목록", "전체 거래 목록 해시를 사용. Merkle Tree·부분 증명 없음."],
  ["난이도", "실습별 1~4개의 선행 0. 실제 Bitcoin의 자동 난이도 조정 없음."],
  [
    "노드·체인 선택",
    "브라우저 내부의 독립 상태 3개. 실제 P2P, 동시 채굴, 분기 선택·재구성 제외. 완전한 분산 합의 구현이 아님.",
  ],
  [
    "확정 상태",
    "블록 포함과 후속 블록 수 표시. 실제 Bitcoin의 확정은 확률적이며 절대적 확정을 의미하지 않음.",
  ],
];
export function Differences() {
  return (
    <details className="differences">
      <summary>실제 Bitcoin과 무엇이 다른가요?</summary>
      <dl>
        {differences.map(([term, description]) => (
          <div key={term}>
            <dt>{term}</dt>
            <dd>{description}</dd>
          </div>
        ))}
      </dl>
    </details>
  );
}
