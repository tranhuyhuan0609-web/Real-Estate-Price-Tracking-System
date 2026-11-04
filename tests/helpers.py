"""Shared test doubles (import as ``from tests.helpers import ...``)."""

from unittest.mock import AsyncMock, MagicMock


def make_mock_pool(mock_cursor):
    """A psycopg AsyncConnectionPool double whose connections yield ``mock_cursor``."""
    mock_conn = AsyncMock()
    mock_conn.transaction = MagicMock()
    mock_conn.transaction.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.transaction.return_value.__aexit__ = AsyncMock(return_value=None)

    mock_conn.cursor = MagicMock()
    mock_conn.cursor.return_value.__aenter__ = AsyncMock(return_value=mock_cursor)
    mock_conn.cursor.return_value.__aexit__ = AsyncMock(return_value=None)

    mock_pool = MagicMock()
    mock_pool.connection.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.connection.return_value.__aexit__ = AsyncMock(return_value=None)
    return mock_pool
