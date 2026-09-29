class NotFound(LookupError):
    """A referenced record (task, area, course, ...) does not exist."""


class AccessDenied(PermissionError):
    """A module tried to touch data outside its permissions."""


class ToolError(Exception):
    """A tool call that cannot run: unknown tool or invalid arguments."""
