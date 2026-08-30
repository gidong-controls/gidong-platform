from __future__ import annotations

from .launch_job import join_launcher_job_from_environment
from .restart import HostRestartController


def main() -> None:
    # Join the launcher-owned Job before Uvicorn or the MCP process can start.
    # Direct developer launches have no launch Job environment and are unchanged.
    join_launcher_job_from_environment()

    import os

    import uvicorn

    from .app import create_app
    from .config import HostConfig

    # ``HostConfig.from_env`` consumes the development API key so it cannot be
    # recovered later from ``os.environ``.  Keep the original inherited
    # process environment solely for the fixed Host-owned replacement helper;
    # it is never returned to a browser or written to diagnostics.
    launch_environment = dict(os.environ)
    config = HostConfig.from_env()
    app = create_app(config)
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=config.bind_host,
            port=config.port,
            workers=1,
            proxy_headers=False,
            server_header=False,
        )
    )
    app.state.request_shutdown = lambda: setattr(server, "should_exit", True)
    restart_controller = HostRestartController.from_current_process(
        port=config.port,
        environment=launch_environment,
    )
    app.state.request_restart = restart_controller.schedule
    server.run()


if __name__ == "__main__":
    main()
