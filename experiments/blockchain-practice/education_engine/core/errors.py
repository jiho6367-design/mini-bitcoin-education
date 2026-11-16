"""검증 결과가 아닌, 요청 자체를 처리할 수 없을 때 사용하는 오류."""


class LabError(Exception):
    """사용자에게 한국어 메시지로 보여 줄 수 있는 실습 오류.

    거래·블록의 검증 실패는 오류가 아니라 ValidationResult로 돌려준다.
    이 오류는 잘못된 입력(400)이나 현재 상태와 맞지 않는 요청(409)에만 쓴다.
    """

    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class LabNotFound(LabError):
    def __init__(self) -> None:
        super().__init__(
            "LAB_NOT_FOUND",
            "실습을 찾을 수 없습니다. 백엔드가 다시 시작되었거나 오래된 실습이 정리되었습니다. "
            "초기화 후 새 실습을 시작하세요.",
            404,
        )
