import { NODE_IDS, type LabSnapshot } from "../api/types";
import { lessonId, type TransactionLesson } from "./learning";
import { transactionPresence, lessonTotals } from "./learningInsights";
import { short } from "./shared";

export function Journey({
  lab,
  history,
  current,
  onSelect,
}: {
  lab: LabSnapshot;
  history: TransactionLesson[];
  current: TransactionLesson | null;
  onSelect: (lesson: TransactionLesson) => void;
}) {
  const entries = history.filter(entry => !current || lessonId(entry) !== lessonId(current));
  if (current?.signed) entries.push(current);
  const lesson = current?.signed ? current : null;
  const choose = (id: string) => {
    const selected = entries.find(entry => lessonId(entry) === id);
    if (selected) onSelect(selected);
  };
  if (!lesson) return entries.length ? (
    <section className="journey" aria-label="거래 여정">
      <label>관찰 거래
        <select value="" onChange={event => choose(event.target.value)}>
          <option value="">현재 거래 작성 중 · 이전 거래 선택</option>
          {entries.map(entry => <option key={lessonId(entry)} value={lessonId(entry)}>{short(lessonId(entry))} · {lessonTotals(entry).sent} 코인</option>)}
        </select>
      </label>
    </section>
  ) : null;
  const totals = lessonTotals(lesson);
  return (
    <section className="journey" aria-label="거래 여정">
      <div className="journey-heading">
        <strong>이 거래는 지금 어디에 있나요?</strong>
        <label>
          관찰 거래
          <select
            value={lessonId(lesson)}
            onChange={(event) => choose(event.target.value)}
          >
            {entries.map((e) => (
              <option key={lessonId(e)} value={lessonId(e)}>
                {short(e.signed?.id ?? "")} · {lessonTotals(e).sent} 코인
              </option>
            ))}
          </select>
        </label>
      </div>
      <p>
        입력 {totals.input} → 송금 {totals.sent} + 거스름돈 {totals.change} +
        수수료 {totals.input - totals.sent - totals.change}
      </p>
      <div className="journey-nodes">
        {NODE_IDS.map((n) => (
          <div key={n}>
            <strong>Node {n}</strong>
            <span data-testid={`presence-${n}`}>{transactionPresence(lab, lesson, n).label}</span>
          </div>
        ))}
      </div>
      <small>
        각 노드의 현재 기록입니다 분기로 체인이 바뀌면 포함 상태도 바뀔 수
        있습니다 새 거래를 작성해도 최근 제출 거래를 다시 선택할 수 있습니다
      </small>
    </section>
  );
}
