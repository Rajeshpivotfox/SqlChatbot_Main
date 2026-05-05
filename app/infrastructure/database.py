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
        """Pre-populate the connection pool."""
        loop = asyncio.get_event_loop()
        for _ in range(self._pool_size):
            conn = await loop.run_in_executor(None, self._create_connection)
            self._pool.append(conn)
        logger.info("database_pool_initialized", pool_size=self._pool_size)

    def _create_connection(self) -> pyodbc.Connection:
        conn = pyodbc.connect(self._connection_string, timeout=self._query_timeout)
        conn.autocommit = True
        cursor = conn.cursor()
        cursor.execute("SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED")
        cursor.close()
        return conn

    @asynccontextmanager
    async def acquire(self):
        """Acquire a connection from the pool."""
        conn = None
        with self._lock:
            if self._pool:
                conn = self._pool.popleft()

        if conn is None:
            loop = asyncio.get_event_loop()
            conn = await loop.run_in_executor(None, self._create_connection)
            logger.warning("pool_exhausted_creating_new_connection")

        try:
            try:
                conn.cursor().execute("SELECT 1").close()
            except pyodbc.Error:
                loop = asyncio.get_event_loop()
                conn = await loop.run_in_executor(None, self._create_connection)
            yield conn
        finally:
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
        return (
            f"DRIVER={{{driver}}};"
            f"SERVER={server};"
            f"DATABASE={database};"
            f"UID={user};"
            f"PWD={password};"
            f"ApplicationIntent=ReadOnly;"
            f"TrustServerCertificate=yes;"
        )
