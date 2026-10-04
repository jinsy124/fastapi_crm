"""Quick database connectivity test script."""

import asyncio
import sys
from sqlalchemy import text
from src.config import settings
from src.db.session import engine


async def check_database_connection() -> None:
    """Verifies that the application can connect to PostgreSQL using current settings."""
    print(f"[*] Testing connection to: {settings.DATABASE_URL.split('@')[-1] if '@' in settings.DATABASE_URL else 'configured DB'}")
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT version();"))
            version = result.scalar()
            print("[+] Database connection SUCCESSFUL!")
            print(f"[+] PostgreSQL Version: {version}")
    except Exception as e:
        print("[-] Database connection FAILED!")
        print(f"[-] Error: {e}")
        print("\nTroubleshooting tips:")
        print("1. Ensure PostgreSQL is installed and running on your machine.")
        print("2. Verify your credentials and database name in .env:")
        print(f"   DATABASE_URL={settings.DATABASE_URL}")
        print("3. If using macOS Postgres / Homebrew:")
        print("   brew services start postgresql@16  (or your installed version)")
        print("   createdb crm_leads")
        sys.exit(1)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(check_database_connection())
