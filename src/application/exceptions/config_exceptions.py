class ConfigNotFoundError(Exception):
    """Raised when the configuration file is not found."""
    pass
class ConfigAlreadyExistsError(Exception):
    """Raised when the configuration file already exists."""
    pass
class ConfigIdentityMismatchError(Exception):
    """Raised when the configuration file identity does not match."""
    pass


class ConfigCorruptedError(ValueError):
    """Raised when a stored crawler configuration is invalid or inconsistent."""
