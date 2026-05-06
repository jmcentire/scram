"""scram CLI.

Subcommands:

- ``scram run`` — start the periodic evaluator + FastAPI server.
- ``scram fire <condition-id> --reason ... --operator ...`` — manually
  trigger a registered condition (HTTP to the running scram service).
- ``scram list`` — list registered conditions.
- ``scram migrate`` — apply DB migrations.

All commands need ``DATABASE_URL`` (Postgres DSN). ``scram fire`` and
``scram list`` additionally need ``SCRAM_API_TOKEN`` (the bearer token).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

from .force_readonly import is_force_readonly_enabled

logger = logging.getLogger("scram.cli")


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _migrations_dir() -> Path:
    """Locate ``migrations/`` relative to the installed package.

    Falls back to ``../migrations`` from this file in the dev tree
    layout. Production deploys carry the migrations alongside the
    package via the Dockerfile COPY.
    """
    # Repo dev tree: src/scram/cli.py -> ../../migrations
    candidate = Path(__file__).resolve().parents[2] / "migrations"
    if candidate.exists():
        return candidate
    # Allow override
    override = os.environ.get("SCRAM_MIGRATIONS_DIR")
    if override:
        return Path(override)
    raise FileNotFoundError(
        "could not locate migrations directory; set SCRAM_MIGRATIONS_DIR"
    )


def _require_dsn() -> str:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL is required", file=sys.stderr)
        sys.exit(2)
    return dsn


# ----------------------------------------------------------------------
# Subcommand: migrate
# ----------------------------------------------------------------------


async def _run_migrate(dsn: str) -> int:
    import asyncpg

    mig_dir = _migrations_dir()
    files = sorted(mig_dir.glob("*.sql"))
    if not files:
        print(f"no migrations found in {mig_dir}")
        return 0

    conn = await asyncpg.connect(dsn=dsn)
    try:
        for f in files:
            sql = f.read_text()
            print(f"applying {f.name}", file=sys.stderr)
            await conn.execute(sql)
    finally:
        await conn.close()
    print(f"applied {len(files)} migration(s)")
    return 0


# ----------------------------------------------------------------------
# Subcommand: run (evaluator + API)
# ----------------------------------------------------------------------


async def _run_server(dsn: str, host: str, port: int) -> int:
    import asyncpg
    import uvicorn

    from .api import create_app
    from .evaluator import Evaluator
    from .registry import Registry

    pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=4)
    if pool is None:  # pragma: no cover — asyncpg signature returns Optional
        raise RuntimeError("asyncpg.create_pool returned None")

    registry = Registry()
    await registry.reload_metadata(pool)
    evaluator = Evaluator(registry=registry, pool=pool)

    if is_force_readonly_enabled():
        # scram itself is a control plane, not a data path; readonly
        # mode here means "do not dispatch any actions, just observe."
        # Documented in docs/force-readonly.md.
        logger.error("SCRAM_FORCE_READONLY=1 — scram running in observe-only mode")

    app = create_app(registry=registry, evaluator=evaluator, pool=pool)

    stop_event = asyncio.Event()
    eval_task = asyncio.create_task(evaluator.run(stop_event=stop_event))

    config = uvicorn.Config(app=app, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)
    try:
        await server.serve()
    finally:
        stop_event.set()
        eval_task.cancel()
        try:
            await eval_task
        except (asyncio.CancelledError, Exception):
            pass
        await pool.close()
    return 0


# ----------------------------------------------------------------------
# Subcommand: fire (HTTP to a running scram)
# ----------------------------------------------------------------------


async def _run_fire(
    condition_id: str, reason: str, operator: str, base_url: str
) -> int:
    import httpx

    token = os.environ.get("SCRAM_API_TOKEN")
    if not token:
        print("SCRAM_API_TOKEN required to fire", file=sys.stderr)
        return 2
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(base_url=base_url, timeout=10.0) as client:
        resp = await client.post(
            "/v1/fire",
            json={
                "condition_id": condition_id,
                "reason": reason,
                "operator": operator,
                "state_snapshot": {"source": "cli"},
            },
            headers=headers,
        )
    print(json.dumps(resp.json(), indent=2))
    return 0 if resp.status_code < 400 else 1


# ----------------------------------------------------------------------
# Subcommand: list (HTTP to a running scram)
# ----------------------------------------------------------------------


async def _run_list(base_url: str) -> int:
    import httpx

    async with httpx.AsyncClient(base_url=base_url, timeout=10.0) as client:
        resp = await client.get("/v1/conditions")
    print(json.dumps(resp.json(), indent=2))
    return 0 if resp.status_code < 400 else 1


# ----------------------------------------------------------------------
# Argparse
# ----------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="scram", description="emergency kill-switch service")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("migrate", help="apply DB migrations")

    runp = sub.add_parser("run", help="start evaluator + API")
    runp.add_argument("--host", default=os.environ.get("SCRAM_HTTP_HOST", "0.0.0.0"))
    runp.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("SCRAM_HTTP_PORT", "8400")),
    )

    firep = sub.add_parser("fire", help="manually fire a condition")
    firep.add_argument("condition_id")
    firep.add_argument("--reason", required=True)
    firep.add_argument("--operator", required=True)
    firep.add_argument(
        "--base-url",
        default=os.environ.get("SCRAM_BASE_URL", "http://127.0.0.1:8400"),
    )

    listp = sub.add_parser("list", help="list registered conditions")
    listp.add_argument(
        "--base-url",
        default=os.environ.get("SCRAM_BASE_URL", "http://127.0.0.1:8400"),
    )

    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=os.environ.get("SCRAM_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "migrate":
        return asyncio.run(_run_migrate(_require_dsn()))
    if args.command == "run":
        return asyncio.run(_run_server(_require_dsn(), args.host, args.port))
    if args.command == "fire":
        return asyncio.run(
            _run_fire(args.condition_id, args.reason, args.operator, args.base_url)
        )
    if args.command == "list":
        return asyncio.run(_run_list(args.base_url))
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
