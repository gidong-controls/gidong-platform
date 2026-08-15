class LauncherError(RuntimeError):
    """Base class for expected launcher failures."""


class LauncherAlreadyRunning(LauncherError):
    """Another launcher owns the single-instance guard."""


class ConfigurationError(LauncherError):
    """Local launcher configuration is invalid."""


class UnexpectedServiceError(LauncherError):
    """The canonical port answered, but it was not the expected Host."""


class HostStartError(LauncherError):
    """The Host could not be started or did not become ready."""


class ElevationError(HostStartError):
    """The Host could not match the XG5000 execution integrity."""


class UiOpenError(LauncherError):
    """No supported UI opening path succeeded."""
