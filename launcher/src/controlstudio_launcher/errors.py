class LauncherError(RuntimeError):
    """Base class for expected launcher failures."""


class LauncherAlreadyRunning(LauncherError):
    """Another launcher owns the single-instance guard."""


class ConfigurationError(LauncherError):
    """Local launcher configuration is invalid."""


class UnexpectedServiceError(LauncherError):
    """A fixed-loopback Host response or handoff did not meet the expected protocol."""


class HostStartError(LauncherError):
    """The launcher could not complete Host startup or browser connection."""


class ElevationError(HostStartError):
    """The validated Windows elevation handoff could not be completed."""


class UiOpenError(LauncherError):
    """No supported UI opening path succeeded."""
