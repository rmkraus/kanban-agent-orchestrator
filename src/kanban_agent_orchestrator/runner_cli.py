import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any


def request_json(server: str, path: str, payload: dict[str, object], psk: str | None = None) -> dict[str, Any] | None:
    data = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if psk:
        headers["Authorization"] = f"Bearer {psk}"
    request = urllib.request.Request(f"{server.rstrip('/')}{path}", data=data, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read().decode("utf-8")
    return json.loads(body) if body and body != "null" else None


def run(args: argparse.Namespace) -> None:
    psk = args.psk or os.environ.get("KANBAN_PSK")
    if not psk:
        raise SystemExit("KANBAN_PSK is required; pass --psk or set the environment variable")

    print(f"runner {args.runner_id} polling {args.server}", flush=True)
    while True:
        try:
            lease = request_json(args.server, "/runner/v1/lease", {"runner_id": args.runner_id, "lease_seconds": args.lease_seconds}, psk=psk)
            if lease is None:
                time.sleep(args.poll_seconds)
                continue
            run_id = str(lease["run"]["id"])
            task = lease["task"]
            print(f"leased task {task['id']}: {task['title']}", flush=True)
            while True:
                time.sleep(max(5, min(args.lease_seconds // 2, 60)))
                request_json(args.server, f"/runner/v1/runs/{run_id}/heartbeat", {"lease_seconds": args.lease_seconds}, psk=psk)
        except KeyboardInterrupt:
            raise
        except Exception as error:  # noqa: BLE001 - CLI daemon should keep running through transient failures.
            print(f"runner error: {error}", file=sys.stderr, flush=True)
            time.sleep(args.poll_seconds)


def read_psk(args: argparse.Namespace) -> str:
    if args.psk_stdin:
        return sys.stdin.read().strip()
    if args.psk:
        return args.psk
    env_psk = os.environ.get("KANBAN_PSK")
    if env_psk:
        return env_psk
    raise SystemExit("PSK required via --psk-stdin, --psk, or KANBAN_PSK")


def install_systemd(args: argparse.Namespace) -> None:
    if os.geteuid() != 0:
        raise SystemExit("install-systemd must run as root; use sudo")
    psk = read_psk(args)
    binary = shutil.which("kanban-runner")
    if binary is None:
        raise SystemExit("kanban-runner not found on PATH")

    env_dir = Path("/etc/kanban-runner")
    env_dir.mkdir(parents=True, exist_ok=True)
    env_path = env_dir / f"{args.name}.env"
    unit_path = Path("/etc/systemd/system") / f"{args.name}.service"

    env_path.write_text(
        "\n".join(
            [
                f"KANBAN_SERVER={args.server}",
                f"KANBAN_RUNNER_ID={args.runner_id}",
                f"KANBAN_PSK={psk}",
                "",
            ]
        )
    )
    env_path.chmod(0o600)

    unit_path.write_text(f"""[Unit]
Description=Kanban Agent Orchestrator Runner ({args.name})
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
EnvironmentFile={env_path}
ExecStart={binary} run --server ${{KANBAN_SERVER}} --runner-id ${{KANBAN_RUNNER_ID}}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
""")
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "enable", "--now", f"{args.name}.service"], check=True)
    print(f"installed and started {args.name}.service")


def uninstall_systemd(args: argparse.Namespace) -> None:
    if os.geteuid() != 0:
        raise SystemExit("uninstall-systemd must run as root; use sudo")
    subprocess.run(["systemctl", "disable", "--now", f"{args.name}.service"], check=False)
    Path("/etc/systemd/system", f"{args.name}.service").unlink(missing_ok=True)
    Path("/etc/kanban-runner", f"{args.name}.env").unlink(missing_ok=True)
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    print(f"removed {args.name}.service")


def main() -> None:
    parser = argparse.ArgumentParser(description="Kanban Agent Orchestrator runner")
    subcommands = parser.add_subparsers(dest="command", required=True)

    run_parser = subcommands.add_parser("run", help="Run the polling runner")
    run_parser.add_argument("--server", default=os.environ.get("KANBAN_SERVER", "http://127.0.0.1:8082"))
    run_parser.add_argument("--runner-id", default=os.environ.get("KANBAN_RUNNER_ID"), required=os.environ.get("KANBAN_RUNNER_ID") is None)
    run_parser.add_argument("--psk", default=None, help="Runner PSK; prefer KANBAN_PSK so it does not appear in ps output")
    run_parser.add_argument("--poll-seconds", type=int, default=3)
    run_parser.add_argument("--lease-seconds", type=int, default=300)
    run_parser.set_defaults(func=run)

    install_parser = subcommands.add_parser("install-systemd", help="Install the runner as a systemd service")
    install_parser.add_argument("--server", required=True)
    install_parser.add_argument("--runner-id", required=True)
    install_parser.add_argument("--name", required=True)
    install_parser.add_argument("--psk", default=None, help="Runner PSK; prefer --psk-stdin or KANBAN_PSK")
    install_parser.add_argument("--psk-stdin", action="store_true", help="Read the runner PSK from stdin")
    install_parser.set_defaults(func=install_systemd)

    uninstall_parser = subcommands.add_parser("uninstall-systemd", help="Remove a systemd runner service")
    uninstall_parser.add_argument("--name", required=True)
    uninstall_parser.set_defaults(func=uninstall_systemd)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
