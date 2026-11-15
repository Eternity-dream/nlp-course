class AppError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        status: int = 400,
        *,
        retryable: bool = False,
        object_id: str | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.retryable = retryable
        self.object_id = object_id

    def payload(self) -> dict:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "object_id": self.object_id,
        }
