"""Database connection and initialization."""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from app.config import DB_PATH

engine = create_async_engine(f"sqlite+aiosqlite:///{DB_PATH}", echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def init_db():
    """Create all tables and run migrations."""
    from app.models import service, module, service_module  # noqa: F401
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Migration: add is_builtin column to modules table
        result = await conn.execute(text("PRAGMA table_info(modules)"))
        columns = [row[1] for row in result.fetchall()]
        if "is_builtin" not in columns:
            await conn.execute(text(
                "ALTER TABLE modules ADD COLUMN is_builtin BOOLEAN NOT NULL DEFAULT 0"
            ))
        if "builtin_source" not in columns:
            await conn.execute(text(
                "ALTER TABLE modules ADD COLUMN builtin_source VARCHAR(64) DEFAULT NULL"
            ))
        # Set builtin_source for existing built-in modules
        await conn.execute(text(
            "UPDATE modules SET builtin_source = name WHERE is_builtin = 1 AND builtin_source IS NULL"
        ))
        # Migration: add requirements column to modules table
        result = await conn.execute(text("PRAGMA table_info(modules)"))
        columns = [row[1] for row in result.fetchall()]
        if "requirements" not in columns:
            await conn.execute(text(
                "ALTER TABLE modules ADD COLUMN requirements TEXT NOT NULL DEFAULT '[]'"
            ))
        # Pre-populate requirements for built-in modules
        await conn.execute(text(
            "UPDATE modules SET requirements = '[\"pymysql>=1.1\"]' WHERE name = 'mysql-helper' AND requirements = '[]'"
        ))
        await conn.execute(text(
            "UPDATE modules SET requirements = '[\"certifi\"]' WHERE name = 'http-client' AND requirements = '[]'"
        ))
        # Migration: add requirements column to services table
        result = await conn.execute(text("PRAGMA table_info(services)"))
        columns = [row[1] for row in result.fetchall()]
        if "requirements" not in columns:
            await conn.execute(text(
                "ALTER TABLE services ADD COLUMN requirements TEXT NOT NULL DEFAULT '[]'"
            ))


async def get_session() -> AsyncSession:
    async with async_session() as session:
        yield session
