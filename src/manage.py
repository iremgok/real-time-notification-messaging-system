"""Operational commands.

    python -m src.manage migrate                 # alembic upgrade head (+ adopt pre-Alembic databases)
    python -m src.manage promote-admin <username>
"""

import argparse
import asyncio

from sqlalchemy import update

from src.core.logger import get_logger, setup_logging
from src.models.database import SessionFactory, dispose_engine
from src.models.entities import User
from src.models.migrations import upgrade_to_head

logger = get_logger("manage")


async def _promote_admin(username: str) -> None:
    async with SessionFactory() as session:
        result = await session.execute(update(User).where(User.username == username).values(is_admin=True))
        await session.commit()
    if result.rowcount == 0:
        raise SystemExit(f"User '{username}' not found")
    logger.info("User promoted to admin")


async def _run(args: argparse.Namespace) -> None:
    try:
        if args.command == "promote-admin":
            await _promote_admin(args.username)
    finally:
        await dispose_engine()


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m src.manage")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate", help="Upgrade the database schema to the latest revision")
    promote = commands.add_parser("promote-admin", help="Grant admin rights to a user")
    promote.add_argument("username")

    args = parser.parse_args()
    setup_logging()
    if args.command == "migrate":
        upgrade_to_head()  # synchronous: Alembic manages its own event loop
        return
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
