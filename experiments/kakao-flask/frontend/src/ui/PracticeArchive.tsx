import { useEffect, useState } from "react";
import type { AnySnapshot } from "../api/types";
import { RequestTimeoutError, withRequestTimeout } from "../api/transport";
import { ApiError } from "../api/client";
import { PREPARING_MESSAGE, serverConnection, serverUnavailableMessage } from "../api/serverConnection";
import { useConnectionMessage } from "./ServerConnection";
import type { Credential } from "./useClassroom";
import { Panel } from "./shared";
import { archiveSearchParams, type ArchiveFilters } from "./archiveSearch";
import { uiText } from "../copy";

export interface StorageStatus {
  id: string;
  savedAt: string | null;
  error: string;
}
interface Practice {
  summary?: { height: number; transactionCount: number; walletCount: number; phase: string };
  id: string;
  title: string;
  created: string;
  updated: string;
}

export async function archiveRequest<T>(
  path: string,
  method = "GET",
  body?: unknown,
  token = "",
): Promise<T> {
  const mutation = method !== "GET";
  try { return await withRequestTimeout(
    async (signal) => {
      let response: Response;
      try {
        response = await fetch(path, {
          signal,
          method,
          headers: {
            "Content-Type": "application/json",
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
          },
          body: body === undefined ? undefined : JSON.stringify(body),
        });
      } catch {
        serverConnection.reportUnavailable();
        throw new ApiError(0, "BACKEND_UNREACHABLE", serverUnavailableMessage(mutation));
      }
      if (!response.ok) {
        const data = await response.json().catch(() => null);
        if ([408, 502, 503, 504].includes(response.status)) {
          serverConnection.reportUnavailable();
          throw new ApiError(response.status, data?.error?.code || "BACKEND_UNREACHABLE",
            data?.error?.message ? `${data.error.message} ${PREPARING_MESSAGE}` : serverUnavailableMessage(mutation));
        }
        throw new ApiError(response.status, data?.error?.code || "ARCHIVE_FAILED",
          data?.error?.message || "저장소 요청을 처리하지 못했습니다");
      }
      if (response.status === 204) return undefined as T;
      // Only the explicit HTML export accepts HTML; a host's loading page is not an archive response.
      if (method === "GET" && path.endsWith("/html") && response.headers.get("content-type")?.includes("text/html"))
        return await response.text() as T;
      const data: unknown = await response.json().catch(() => null);
      if (data === null) {
        serverConnection.reportUnavailable();
        throw new ApiError(response.status, "BACKEND_UNREACHABLE", serverUnavailableMessage(mutation));
      }
      return data as T;
    },
    { mutation },
  ); } catch (error) {
    if (error instanceof RequestTimeoutError) serverConnection.reportUnavailable();
    throw error;
  }
}

export function PracticeArchive({
  classroom = false,
  token = "",
  name = "교사",
  storage,
  onResume,
}: {
  classroom?: boolean;
  token?: string;
  name?: string;
  storage?: StorageStatus;
  onResume: (value: { lab: AnySnapshot } | Credential) => void;
}) {
  const base = classroom ? "/api/education/classroom/practices" : "/api/education/practices";
  const [items, setItems] = useState<Practice[] | null>(null);
  const [query, setQuery] = useState("");
  const [selectedDate, setSelectedDate] = useState("");
  const [applied, setApplied] = useState<ArchiveFilters>({ q: "", date: "" });
  const [offset, setOffset] = useState(0);
  const [total, setTotal] = useState(0);
  const pageSize = 20;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [status, setStatus] = useState<StorageStatus | undefined>(storage);
  useEffect(() => {
    if (!storage?.id) return;
    let active = true;
    let updating = false;
    async function update() {
      if (updating || serverConnection.getSnapshot().phase !== "ready") return;
      updating = true;
      try {
        const next = await archiveRequest<StorageStatus>(
          `${base}/${storage!.id}/status`,
          "GET",
          undefined,
          token,
        );
        if (active) setStatus(next);
      } catch {
        /* main API displays connection errors; never claim a newer save */
      } finally {
        updating = false;
      }
    }
    void update();
    const timer = setInterval(() => void update(), 2000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [storage?.id, base, token]);
  const current = status?.id === storage?.id ? status : storage;
  const [view, setView] = useState("");
  const [editing, setEditing] = useState("");
  const [title, setTitle] = useState("");
  const [deleting, setDeleting] = useState("");
  const connectionError = useConnectionMessage(error || current?.error || "");
  async function run(action: () => Promise<void>) {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await action();
    } catch (e) {
      setError(e instanceof Error ? e.message : "저장소 연결 실패");
    } finally {
      setBusy(false);
    }
  }
  async function refresh(nextOffset = offset, filters = applied) {
    const readPage = (pageOffset: number) =>
      archiveRequest<{ practices: Practice[]; total: number }>(
        `${base}?${archiveSearchParams(filters, pageOffset, pageSize)}`,
        "GET",
        undefined,
        token,
      );
    let data = await readPage(nextOffset);
    // Deleting the final item on a page must not leave a false empty archive.
    if (data.total > 0 && nextOffset >= data.total) {
      nextOffset = Math.floor((data.total - 1) / pageSize) * pageSize;
      data = await readPage(nextOffset);
    }
    setItems(data.practices);
    setOffset(nextOffset);
    setTotal(data.total ?? data.practices.length);
    setApplied(filters);
  }
  const date = (value: string) => new Date(value).toLocaleString("ko-KR");
  return (
    <section className="practice-archive" aria-label="실습 저장소">
      <div className="button-row">
        {storage && (
          <span role="status">
            {current?.error
              ? "⚠ 저장되지 않음 · 실행 일시정지"
              : current?.savedAt
                ? `✓ 저장됨 · ${date(current!.savedAt!)}`
                : "첫 저장 대기"}
          </span>
        )}
        <button
          className="secondary"
          disabled={busy}
          onClick={() => void run(() => refresh(0))}
        >
          저장된 {classroom ? "수업" : "실습"}
        </button>
        {storage && (
          <button
            className="secondary"
            disabled={busy}
            onClick={() =>
              void run(async () => {
                const result = await archiveRequest<{ savedAt: string }>(
                  `${base}/${storage.id}/save`,
                  "POST",
                  {},
                  token,
                );
                setStatus({
                  id: storage.id,
                  savedAt: result.savedAt,
                  error: "",
                });
              })
            }
          >
            지금 저장
          </button>
        )}
      </div>
      {(error || current?.error) && (
        <p role="alert">{uiText(connectionError)}</p>
      )}
      {items && (
        <Panel title={classroom ? "저장된 수업" : "저장된 실습"}>
          <p>
            실습 기록은 서버에 자동 저장됩니다 이어 하기는 멈춘 상태로 열립니다
            HTML은 결과 확인용 파일입니다
          </p>
          <button
            className="text-button"
            onClick={() => {
              setItems(null);
              setView("");
            }}
          >
            목록 닫기
          </button>
          <form className="archive-search" onSubmit={(event) => { event.preventDefault(); void run(() => refresh(0, { q: query, date: selectedDate })); }}>
            <label>실습 이름 검색<input aria-label="저장 기록 검색" type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="실습 이름" /></label>
            <label>저장 날짜<input aria-label="저장 날짜 검색" type="date" value={selectedDate} disabled={busy} onChange={(event) => {
              const value = event.target.value;
              setSelectedDate(value);
              void run(() => refresh(0, { q: query, date: value }));
            }} /></label>
            <button disabled={busy}>검색</button>
          </form>
          <p className="muted">저장 날짜는 아래 시각과 같은 이 브라우저의 현지 날짜를 기준으로 합니다</p>
          <p role="status">검색 결과 {total}개 · {offset + (items.length ? 1 : 0)}–{offset + items.length}번째</p>
          {!items.length && (
            <p>{applied.q.trim() || applied.date ? "검색한 이름이나 날짜에 맞는 기록이 없습니다" : "아직 저장된 기록이 없습니다 새 실습을 시작해 보세요"}</p>
          )}
          <div className="archive-pagination">
            <button disabled={busy || offset === 0} onClick={() => void run(() => refresh(Math.max(0, offset - pageSize)))}>이전 기록</button>
            <button disabled={busy || offset + items.length >= total} onClick={() => void run(() => refresh(offset + pageSize))}>다음 기록</button>
          </div>
          {items.map((item) => (
            <article className="archive-item" key={item.id}>
              <strong>{item.title}</strong>
              <small>저장 {date(item.updated)}</small>
              {item.summary && <p className="archive-progress">{item.summary.phase === "setup" ? "지갑 준비 중" : "진행한 실습"} · 블록 높이 {item.summary.height} · 일반 거래 {item.summary.transactionCount}개 · 지갑 {item.summary.walletCount}개</p>}
              <div className="button-row">
                <button
                  disabled={busy}
                  onClick={() =>
                    void run(async () => {
                      setView(
                        await archiveRequest<string>(
                          `${base}/${item.id}/html`,
                          "GET",
                          undefined,
                          token,
                        ),
                      );
                    })
                  }
                >
                  결과 보기
                </button>
                <button
                  disabled={busy}
                  onClick={() =>
                    void run(async () => {
                      const result = await archiveRequest<
                        { lab: AnySnapshot } | Credential
                      >(`${base}/${item.id}/resume`, "POST", { name }, token);
                      onResume(result);
                      setItems(null);
                      setView("");
                    })
                  }
                >
                  이어 하기
                </button>
                <button
                  className="secondary"
                  disabled={busy}
                  onClick={() =>
                    void run(async () => {
                      const html = await archiveRequest<string>(
                        `${base}/${item.id}/html`,
                        "GET",
                        undefined,
                        token,
                      );
                      const url = URL.createObjectURL(
                        new Blob([html], { type: "text/html;charset=utf-8" }),
                      );
                      const link = document.createElement("a");
                      link.href = url;
                      link.download = `실습-${item.id}.html`;
                      link.click();
                      setTimeout(() => URL.revokeObjectURL(url), 1000);
                    })
                  }
                >
                  HTML로 저장
                </button>
                <button
                  className="text-button"
                  disabled={busy}
                  onClick={() => {
                    setEditing(item.id);
                    setTitle(item.title);
                  }}
                >
                  이름 변경
                </button>
                <button
                  className="text-button"
                  disabled={busy}
                  onClick={() => setDeleting(item.id)}
                >
                  기록 삭제
                </button>
              </div>
              {editing === item.id && (
                <form
                  className="button-row"
                  onSubmit={(e) => {
                    e.preventDefault();
                    void run(async () => {
                      await archiveRequest(
                        `${base}/${item.id}/rename`,
                        "POST",
                        { title },
                        token,
                      );
                      setEditing("");
                      await refresh();
                    });
                  }}
                >
                  <label>
                    실습 이름{" "}
                    <input
                      required
                      maxLength={100}
                      value={title}
                      onChange={(e) => setTitle(e.target.value)}
                    />
                  </label>
                  <button disabled={busy}>이름 저장</button>
                  <button type="button" onClick={() => setEditing("")}>
                    취소
                  </button>
                </form>
              )}
              {deleting === item.id && (
                <div role="group" aria-label="기록 삭제 확인">
                  <p>
                    이 실습의 저장 상태와 활동 기록을 삭제합니다 되돌리려면 DB
                    백업이 필요합니다
                  </p>
                  <div className="button-row">
                    <button
                      disabled={busy}
                      onClick={() =>
                        void run(async () => {
                          await archiveRequest(
                            `${base}/${item.id}`,
                            "DELETE",
                            undefined,
                            token,
                          );
                          setDeleting("");
                          setView("");
                          await refresh();
                        })
                      }
                    >
                      삭제 확인
                    </button>
                    <button onClick={() => setDeleting("")}>취소</button>
                  </div>
                </div>
              )}
            </article>
          ))}
          {view && (
            <div>
              <button onClick={() => setView("")}>결과 닫기</button>
              <iframe
                className="archive-preview"
                title="저장된 실습 결과"
                sandbox=""
                srcDoc={view}
              />
            </div>
          )}
        </Panel>
      )}
    </section>
  );
}
