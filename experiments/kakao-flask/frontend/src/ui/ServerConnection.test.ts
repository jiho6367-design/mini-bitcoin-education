import { expect, it } from "vitest";
import { OFFLINE_MESSAGE, PREPARING_MESSAGE } from "../api/serverConnection";
import { connectionMessage } from "./ServerConnection";

it("연결이 복구돼도 처리 여부가 불확실한 작업 안내를 보존한다", () => {
  const uncertainty = " 작업이 처리됐을 수 있으므로 현재 상태를 확인한 뒤 다시 시도하세요";
  expect(connectionMessage(PREPARING_MESSAGE + uncertainty, "ready"))
    .toBe("서버 연결이 복구되었습니다" + uncertainty);
  expect(connectionMessage(OFFLINE_MESSAGE + uncertainty, "ready"))
    .toBe("인터넷 연결이 복구되었습니다" + uncertainty);
  expect(connectionMessage("저장 실패로 변경을 원복했습니다 " + PREPARING_MESSAGE, "ready"))
    .toBe("저장 실패로 변경을 원복했습니다 서버 연결이 복구되었습니다");
});

it("연결 대기 중 안내와 권한·검증·저장 실패 원문은 바꾸지 않는다", () => {
  expect(connectionMessage(PREPARING_MESSAGE, "preparing")).toBe(PREPARING_MESSAGE);
  for (const message of ["교사만 실행할 수 있습니다", "저장에 실패했습니다", "서버 준비 중 다른 오류"]) {
    expect(connectionMessage(message, "ready")).toBe(message);
  }
});
