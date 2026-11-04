"""Domain and configuration exceptions."""


class ConfigNotFoundError(Exception):
    """Raised when the configuration is not found."""

    pass


class ConfigAlreadyExistsError(Exception):
    """Raised when the configuration already exists."""

    pass


class ConfigCorruptedError(ValueError):
    """Raised when a stored crawler configuration is invalid or inconsistent."""

    pass
