export class RequestTimeoutError extends Error {
  constructor(mutation: boolean) {
    super(
      mutation
        ? "서버 응답이 지연되어 기다리기를 멈췄습니다 작업이 처리됐을 수 있으므로 현재 상태를 확인한 뒤 다시 시도하세요"
        : "서버 응답이 지연되어 기다리기를 멈췄습니다 연결 상태를 확인한 뒤 다시 조회하세요",
    );
    this.name = "RequestTimeoutError";
  }
}

/** 요청 접수 뒤 응답이 사라져도 화면 잠금이 무한히 유지되지 않게 한다
 * 변경 요청은 자동 재전송하지 않는다 서버 처리 여부는 별도로 확인해야 한다
 */
export async function withRequestTimeout<T>(
  run: (signal: AbortSignal) => Promise<T>,
  { mutation = false, timeoutMs = 15_000 } = {},
): Promise<T> {
  const controller = new AbortController();
  let timer: ReturnType<typeof setTimeout> | undefined;
  const expired = new Promise<never>((_, reject) => {
    timer = setTimeout(() => {
      reject(new RequestTimeoutError(mutation));
      controller.abort();
    }, timeoutMs);
  });
  try {
    return await Promise.race([run(controller.signal), expired]);
  } finally {
    clearTimeout(timer);
  }
}
