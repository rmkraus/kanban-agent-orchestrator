import argparse
import asyncio

import uvicorn

from kanban_agent_orchestrator.app import create_public_app, create_runner_app
from kanban_agent_orchestrator.kernel import OrchestratorKernel


def serve_public(host: str, port: int, reload: bool) -> None:
    uvicorn.run("kanban_agent_orchestrator.app:app", host=host, port=port, reload=reload)


def serve_runner(host: str, port: int, reload: bool) -> None:
    uvicorn.run("kanban_agent_orchestrator.app:runner_app", host=host, port=port, reload=reload)


async def serve_both(public_host: str, public_port: int, runner_host: str, runner_port: int) -> None:
    kernel = OrchestratorKernel.persistent()
    public_server = uvicorn.Server(uvicorn.Config(create_public_app(kernel), host=public_host, port=public_port))
    runner_server = uvicorn.Server(uvicorn.Config(create_runner_app(kernel), host=runner_host, port=runner_port))
    await asyncio.gather(public_server.serve(), runner_server.serve())


def main() -> None:
    parser = argparse.ArgumentParser(description="Kanban Agent Orchestrator")
    parser.add_argument("--host", default="127.0.0.1", help="Public API/UI host to bind")
    parser.add_argument("--port", type=int, default=8080, help="Public API/UI port to bind")
    parser.add_argument("--runner-host", default=None, help="Runner API host to bind; defaults to --host")
    parser.add_argument("--runner-port", type=int, default=8082, help="Runner-only API port to bind")
    parser.add_argument("--public-only", action="store_true", help="Serve only the public API/UI")
    parser.add_argument("--runner-only", action="store_true", help="Serve only the runner API")
    parser.add_argument("--reload", action="store_true", help="Enable uvicorn reload; only valid with --public-only or --runner-only")
    args = parser.parse_args()

    runner_host = args.runner_host or args.host
    if args.public_only and args.runner_only:
        raise SystemExit("--public-only and --runner-only are mutually exclusive")
    if args.reload and not (args.public_only or args.runner_only):
        raise SystemExit("--reload can only be used with --public-only or --runner-only")

    if args.runner_only:
        serve_runner(runner_host, args.runner_port, args.reload)
        return
    if args.public_only:
        serve_public(args.host, args.port, args.reload)
        return

    asyncio.run(serve_both(args.host, args.port, runner_host, args.runner_port))


if __name__ == "__main__":
    main()
