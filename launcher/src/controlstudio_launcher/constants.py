from __future__ import annotations

LAUNCHER_VERSION = "0.1.0"
EXPECTED_HOST_SERVICE = "controlstudio-host"
EXPECTED_HOST_API_VERSION = "1"
DEFAULT_EXPECTED_HOST_BUILD = LAUNCHER_VERSION

CANONICAL_BIND_HOST = "127.0.0.1"
CANONICAL_BROWSER_HOST = CANONICAL_BIND_HOST
CANONICAL_PORT = 8765
CANONICAL_BASE_URL = f"http://{CANONICAL_BROWSER_HOST}:{CANONICAL_PORT}"
CANONICAL_PROBE_BASE_URL = f"http://{CANONICAL_BIND_HOST}:{CANONICAL_PORT}"
# Probe the exact IPv4 loopback address the Host binds. On some Windows
# installations `localhost` resolves to IPv6 first and an absent IPv6 listener
# times out instead of returning a deterministic connection refusal.
CANONICAL_HEALTH_URL = f"{CANONICAL_PROBE_BASE_URL}/api/health"
CANONICAL_CHALLENGE_URL = f"{CANONICAL_PROBE_BASE_URL}/api/launcher/challenge"
CANONICAL_HANDOFF_URL = f"{CANONICAL_PROBE_BASE_URL}/api/launcher/handoff"

WINDOWS_MUTEX_NAME = r"Local\ControlStudio.Launcher.v1"
