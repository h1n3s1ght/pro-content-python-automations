class OperationCanceled(Exception):
    """Raised when a job was canceled by the user."""


class PauseRequested(Exception):
    """Raised when a job was paused by the user."""
