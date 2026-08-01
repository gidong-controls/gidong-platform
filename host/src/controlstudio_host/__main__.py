from __future__ import annotations

from .launch_job import join_launcher_job_from_environment


def main() -> None:
    # Join the launcher-owned Job before Uvicorn or the MCP process can start.
    # Direct developer launches have no launch Job environment and are unchanged.
    join_launcher_job_from_environment()

    import uvicorn

    from .app import create_app
    from .config import HostConfig

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
    server.run()


if __name__ == "__main__":
    main()
