class ReproError(Exception):
    """An actionable user-facing error with a stable launcher exit code."""

    def __init__(self, message: str, code: int = 3):
        super().__init__(message)
        self.code = code
