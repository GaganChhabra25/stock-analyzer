"""Map changed repository paths to production processes that need deployment."""

from __future__ import annotations

import argparse
from dataclasses import dataclass


DOCKER_SERVICES = ("cron-worker", "crudeoil-ws", "nifty-ws")

SHARED_DOCKER_PATHS = {
    "docker-compose.yml",
    "docker/cron-worker/Dockerfile",
    "requirements.txt",
    "config.py",
    "logging_config.py",
    "options/greeks.py",
    "options/kite_auth.py",
    "options/tg.py",
    "screener/db.py",
}

CRON_PATHS = {
    "deploy/contabo/crontab",
    "deploy/contabo/crontab.docker",
    "options/collector.py",
    "options/db_summary.py",
    "options/derived.py",
    "options/instruments.py",
    "options/mcx_ohlc.py",
    "options/notifier.py",
    "options/nifty_retention.py",
    "options/nse_ohlc.py",
    "options/save_instruments.py",
    "options/watchdog.py",
}

NIFTY_PATHS = {
    "options/nifty_feature_backfill.py",
    "options/nifty_ws.py",
}


@dataclass(frozen=True)
class DeploymentPlan:
    deploy_web: bool
    services: tuple[str, ...]


def _is_web_path(path: str) -> bool:
    if path == "app.py" or path.startswith(("templates/", "static/", "screener/")):
        return True
    # The web process imports options modules. Restarting it is cheap and avoids
    # leaving an old in-memory module after an options code deployment.
    return path.startswith("options/") and path.endswith(".py")


def deployment_plan(paths: list[str], deploy_all: bool = False) -> DeploymentPlan:
    normalized = {path.replace("\\", "/").lstrip("./") for path in paths if path.strip()}
    if deploy_all:
        return DeploymentPlan(True, DOCKER_SERVICES)

    services: set[str] = set()
    deploy_web = any(_is_web_path(path) for path in normalized)

    if normalized & SHARED_DOCKER_PATHS:
        services.update(DOCKER_SERVICES)
        deploy_web = True

    if "options/crudeoil_ws.py" in normalized:
        services.add("crudeoil-ws")
    if normalized & NIFTY_PATHS:
        services.add("nifty-ws")
    if normalized & CRON_PATHS or any(path.startswith("options/exchange/") for path in normalized):
        services.add("cron-worker")

    return DeploymentPlan(
        deploy_web=deploy_web,
        services=tuple(service for service in DOCKER_SERVICES if service in services),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="*")
    parser.add_argument("--all", action="store_true", dest="deploy_all")
    args = parser.parse_args()
    plan = deployment_plan(args.paths, deploy_all=args.deploy_all)
    print(f"web={'true' if plan.deploy_web else 'false'}")
    print(f"services={' '.join(plan.services)}")


if __name__ == "__main__":
    main()
