"""Operational commands.

    python -m src.manage init-db                 # create tables (run once before starting workers)
    python -m src.manage promote-admin <username>
"""

import argparse
import asyncio

from sqlalchemy import update

from src.core.logger import get_logger, setup_logging
from src.models.database import SessionFactory, create_tables, dispose_engine
from src.models.entities import User

logger = get_logger("manage")


async def _init_db() -> None:
    await create_tables()
    logger.info("Database schema is up to date")


async def _promote_admin(username: str) -> None:
    async with SessionFactory() as session:
        result = await session.execute(update(User).where(User.username == username).values(is_admin=True))
        await session.commit()
    if result.rowcount == 0:
        raise SystemExit(f"User '{username}' not found")
    logger.info("User promoted to admin")


async def _run(args: argparse.Namespace) -> None:
    try:
        if args.command == "init-db":
            await _init_db()
        elif args.command == "promote-admin":
            await _promote_admin(args.username)
    finally:
        await dispose_engine()


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m src.manage")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init-db", help="Create database tables")
    promote = commands.add_parser("promote-admin", help="Grant admin rights to a user")
    promote.add_argument("username")

    setup_logging()
    asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    main()
