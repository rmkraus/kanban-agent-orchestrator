class OrchestratorError(Exception):
    """Base error for orchestrator domain failures."""


class NotFoundError(OrchestratorError):
    """Raised when a requested entity does not exist."""


class DependencyCycleError(OrchestratorError):
    """Raised when adding a dependency would create a cycle."""


class InvalidTransitionError(OrchestratorError):
    """Raised when a state transition is not valid."""
