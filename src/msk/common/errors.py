"""MSK error types and exceptions."""


class MSKError(Exception):
    """Base exception for all MSK errors."""

    def __init__(self, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def __str__(self) -> str:
        if self.hint:
            return f"{self.message}\nHint: {self.hint}"
        return self.message


class NotAProjectError(MSKError):
    """Raised when an operation is executed outside an initialized MSK project."""

    def __init__(
        self,
        message: str = "Not inside an MSK project.",
        hint: str = "Run 'msk init' to initialize project intelligence.",
    ) -> None:
        super().__init__(message, hint=hint)


class ProjectAlreadyInitializedError(MSKError):
    """Raised when msk init is invoked on an already initialized project without force."""

    def __init__(
        self,
        message: str = "MSK is already initialized in this project.",
        hint: str = "Use 'msk update' to refresh or 'msk init --force' to recreate.",
    ) -> None:
        super().__init__(message, hint=hint)


class ScannerError(MSKError):
    """Raised when filesystem scanning encounters an unrecoverable error."""


class ParserError(MSKError):
    """Raised when code parsing fails fatally."""


class StorageError(MSKError):
    """Raised when SQLite operations fail."""


class PolicyError(MSKError):
    """Raised when security or privacy policy evaluation fails."""
