from __future__ import annotations


class WorkflowRequestError(ValueError):
    """Base class for safe, caller-caused workflow request failures."""


class ProjectNotFoundError(WorkflowRequestError):
    """The requested project reference resolves to no project."""


class AmbiguousProjectReferenceError(WorkflowRequestError):
    """The requested project reference resolves to multiple projects."""


class InvalidWorkflowRequestError(WorkflowRequestError):
    """The caller supplied an invalid workflow request."""
