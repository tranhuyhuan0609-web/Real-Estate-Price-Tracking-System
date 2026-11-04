"""Single active scheduler via a PostgreSQL session-level advisory lock.

The lock lives on a dedicated connection (not the pool): Postgres releases it automatically when
that connection ends, so a crashed leader is replaced by a standby on its next poll.
"""

from __future__ import annotations

import logging

import psycopg

logger = logging.getLogger(__name__)

# Arbitrary, fixed 64-bit key shared by every scheduler of this application.
SCHEDULER_LOCK_KEY = 0x5245_5354_5343_4844  # "RESTSCHD"


class PgAdvisoryLeaderLock:
    def __init__(self, dsn: str, key: int = SCHEDULER_LOCK_KEY) -> None:
        self._dsn = dsn
        self._key = key
        self._conn: psycopg.AsyncConnection | None = None
        self.is_leader = False

    async def ensure(self) -> bool:
        """Hold or try to take the lock; returns whether this process is the leader now."""
        try:
            if self._conn is None or self._conn.closed:
                self.is_leader = False
                self._conn = await psycopg.AsyncConnection.connect(self._dsn, autocommit=True)
            if self.is_leader:
                await self._conn.execute("SELECT 1")  # still connected -> still holding it
                return True
            cur = await self._conn.execute("SELECT pg_try_advisory_lock(%s)", (self._key,))
            row = await cur.fetchone()
            self.is_leader = bool(row and row[0])
        except psycopg.Error as exc:
            logger.warning("Leader lock check failed (%s); acting as standby.", exc)
            await self.close()
        return self.is_leader

    async def close(self) -> None:
        self.is_leader = False
        if self._conn is not None and not self._conn.closed:
            await self._conn.close()  # ending the session releases the lock
        self._conn = None
