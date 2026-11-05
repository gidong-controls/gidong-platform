from __future__ import annotations

from .launch_job import join_launcher_job_from_environment
from .restart import HostRestartController

_STARTUP_STAGE_MODULES = {
    "main_entered": ("controlstudio_host.__main__", "__main__.py"),
    "launch_job_joined": ("controlstudio_host.launch_job", "launch_job.py"),
    "uvicorn_imported": ("uvicorn", "__init__.py"),
    "app_imported": ("controlstudio_host.app", "app.py"),
    "config_loaded": ("controlstudio_host.config", "config.py"),
    "app_created": ("controlstudio_host.app", "app.py"),
    "server_ready_to_run": ("controlstudio_host.__main__", "__main__.py"),
}


def _startup_clock():
    try:
        import time

        return time.monotonic()
    except BaseException:
        # Diagnostics must not become another startup dependency.
        return None


def _startup_stage(stage, started) -> None:
    try:
        import sys
        import time

        expected = _STARTUP_STAGE_MODULES.get(stage)
        if expected is None or started is None or sys.stderr is None:
            return
        module_name, filename = expected
        module = sys.modules.get(
            __name__ if module_name == "controlstudio_host.__main__" else module_name
        )
        origin = getattr(module, "__file__", None)
        spec = getattr(module, "__spec__", None)
        # Compare module metadata only. No origin/path values are printed and
        # this boolean is not an integrity check or a health/readiness claim.
        matches = (
            isinstance(origin, str)
            and getattr(spec, "name", None) == module_name
            and origin.replace("\\", "/").rsplit("/", 1)[-1] == filename
        )
        if matches and module_name.startswith("controlstudio_host."):
            matches = (
                origin.replace("\\", "/").rsplit("/", 1)[0]
                == __file__.replace("\\", "/").rsplit("/", 1)[0]
            )
        elapsed_ms = max(0, int((time.monotonic() - started) * 1000))
        print(
            "CONTROLSTUDIO_HOST_STARTUP "
            f"stage={stage} elapsed_ms={elapsed_ms} "
            f"module_identity_matches_expected={str(matches).lower()}",
            file=sys.stderr,
            flush=True,
        )
    except BaseException:
        # Preserve the original startup behavior even with missing/failed stderr.
        pass


def _build_server_config(uvicorn_module, app, config):
    return uvicorn_module.Config(
        app,
        host=config.bind_host,
        port=config.port,
        workers=1,
        proxy_headers=False,
        server_header=False,
        # Bound HTTP connection draining, not model inference. A Windows
        # transport teardown error can otherwise leave wait_closed pending
        # forever and prevent lifespan cleanup and the replacement Host.
        timeout_graceful_shutdown=10.0,
    )


def main() -> None:
    startup_started = _startup_clock()
    _startup_stage("main_entered", startup_started)
    # Join the launcher-owned Job before Uvicorn or the MCP process can start.
    # Direct developer launches have no launch Job environment and are unchanged.
    join_launcher_job_from_environment()
    _startup_stage("launch_job_joined", startup_started)

    import os

    import uvicorn
    _startup_stage("uvicorn_imported", startup_started)

    from .app import create_app
    _startup_stage("app_imported", startup_started)
    from .config import HostConfig

    # ``HostConfig.from_env`` consumes the development API key so it cannot be
    # recovered later from ``os.environ``.  Keep the original inherited
    # process environment solely for the fixed Host-owned replacement helper;
    # it is never returned to a browser or written to diagnostics.
    launch_environment = dict(os.environ)
    config = HostConfig.from_env()
    _startup_stage("config_loaded", startup_started)
    app = create_app(config)
    _startup_stage("app_created", startup_started)
    server = uvicorn.Server(_build_server_config(uvicorn, app, config))
    app.state.request_shutdown = lambda: setattr(server, "should_exit", True)
    restart_controller = HostRestartController.from_current_process(
        port=config.port,
        environment=launch_environment,
    )
    app.state.request_restart = restart_controller.schedule
    _startup_stage("server_ready_to_run", startup_started)
    server.run()


if __name__ == "__main__":
    main()
