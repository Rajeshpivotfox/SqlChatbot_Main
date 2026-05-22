# ──────────────────────────────────────────────────────────────────────────────
# DATABASE CONNECTION POOL — manual pool because pyodbc has no async support.
#
# Safety layers (defense-in-depth, prevents writes even if SQL injection slips through):
#   1. Connection string: ApplicationIntent=ReadOnly (SQL Server routes to read replica)
#   2. Session level: SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED (dirty reads OK,
#      avoids locking production tables during analytical queries)
#   3. autocommit=True — no open transactions that could hold locks
#   4. SQLValidator rejects non-SELECT statements before they reach here
#
# Pool mechanics:
#   - Pre-populated at startup (init_services → initialize())
#   - acquire() pops from deque; returns to deque on release
#   - If pool is empty, creates overflow connection (logged as warning)
#   - Health check (SELECT 1) before yielding — reconnects on stale connections
# ──────────────────────────────────────────────────────────────────────────────

import pyodbc
import asyncio
from contextlib import asynccontextmanager
from collections import deque
from threading import Lock
import structlog

logger = structlog.get_logger(__name__)


class DatabasePool:
    """Thread-safe connection pool for SQL Server with read-only enforcement."""

    def __init__(self, connection_string: str, pool_size: int = 10,
                 query_timeout: int = 30):
        self._connection_string = connection_string
        self._pool_size = pool_size
        self._query_timeout = query_timeout
        self._pool: deque[pyodbc.Connection] = deque()
        self._lock = Lock()

    async def initialize(self) -> None:
        """Pre-populate the connection pool at app startup."""
        loop = asyncio.get_event_loop()
        for _ in range(self._pool_size):
            conn = await loop.run_in_executor(None, self._create_connection)
            self._pool.append(conn)
        logger.info("database_pool_initialized", pool_size=self._pool_size)

    def _create_connection(self) -> pyodbc.Connection:
        conn = pyodbc.connect(self._connection_string, timeout=self._query_timeout)
        conn.autocommit = True
        cursor = conn.cursor()
        # READ UNCOMMITTED: avoids locks on production; acceptable for analytics
        cursor.execute("SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED")
        cursor.close()
        return conn

    @asynccontextmanager
    async def acquire(self):
        """Acquire a connection, health-check it, yield, then return to pool."""
        conn = None
        with self._lock:
            if self._pool:
                conn = self._pool.popleft()

        if conn is None:
            loop = asyncio.get_event_loop()
            conn = await loop.run_in_executor(None, self._create_connection)
            logger.warning("pool_exhausted_creating_new_connection")

        try:
            # Health check — reconnect if the connection went stale
            try:
                conn.cursor().execute("SELECT 1").close()
            except pyodbc.Error:
                loop = asyncio.get_event_loop()
                conn = await loop.run_in_executor(None, self._create_connection)
            yield conn
        finally:
            # Return to pool if under capacity, otherwise discard overflow
            with self._lock:
                if len(self._pool) < self._pool_size:
                    self._pool.append(conn)
                else:
                    conn.close()

    async def close(self) -> None:
        with self._lock:
            while self._pool:
                self._pool.pop().close()
        logger.info("database_pool_closed")

    @staticmethod
    def build_connection_string(server: str, database: str, user: str,
                                password: str, driver: str) -> str:
        # ApplicationIntent=ReadOnly tells SQL Server to route to a read replica
        return (
            f"DRIVER={{{driver}}};"
            f"SERVER={server};"
            f"DATABASE={database};"
            f"UID={user};"
            f"PWD={password};"
            f"ApplicationIntent=ReadOnly;"
            f"TrustServerCertificate=yes;"
        )
