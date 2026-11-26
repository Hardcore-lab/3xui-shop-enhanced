import pytest
from sqlalchemy import inspect

from app import config
from app.db.database import Database


@pytest.mark.asyncio
async def test_initialize_fresh_sqlite_database(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DEFAULT_DATA_DIR", tmp_path)
    db = Database(
        config.DatabaseConfig(HOST=None, PORT=None, NAME="test", USERNAME=None, PASSWORD=None)
    )
    try:
        await db.initialize()
        await db.initialize()
        async with db.engine.connect() as connection:
            indexes = await connection.run_sync(
                lambda sync_connection: inspect(sync_connection).get_indexes("user_clients")
            )
        assert any(
            index["name"] == "ix_user_clients_client_email" and index["unique"]
            for index in indexes
        )
    finally:
        await db.close()
