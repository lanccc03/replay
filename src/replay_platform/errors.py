class ReplayError(Exception):
    """Base application error."""


class DependencyUnavailableError(ReplayError):
    """Raised when an optional runtime dependency is missing."""


class ConfigurationError(ReplayError):
    """Raised when scenario or adapter configuration is invalid."""


class AdapterOperationError(ReplayError):
    """Raised when an adapter operation fails."""


class TraceFormatError(ReplayError):
    """Raised when a trace file cannot be parsed."""


class DiagnosticError(ReplayError):
    """Raised for UDS or DoIP failures."""

