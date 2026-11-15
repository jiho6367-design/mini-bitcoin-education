import { serverUnavailableMessage } from "./serverConnection";

export class RequestTimeoutError extends Error {
  constructor(mutation: boolean) {
    super(serverUnavailableMessage(mutation));
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
