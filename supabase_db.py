import os
import re
import urllib.parse
import threading
import asyncio
import logging
from typing import Any, List, Optional, Dict, Tuple, Union

try:
    import asyncpg
except ImportError:
    asyncpg = None

logger = logging.getLogger("supabase_db")

# Default connection strings requested by user
DEFAULT_DATABASE_URL = "postgresql://postgres.ptvrjqwdevrcszhpuvug:ony@139245spnn@aws-0-ap-northeast-2.pooler.supabase.com:6543/postgres?pgbouncer=true"
DEFAULT_DIRECT_URL = "postgresql://postgres.ptvrjqwdevrcszhpuvug:ony@139245spnn@aws-0-ap-northeast-2.pooler.supabase.com:5432/postgres"

DATABASE_URL = os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)
DIRECT_URL = os.getenv("DIRECT_URL", DEFAULT_DIRECT_URL)

def parse_db_url(url: str) -> Dict[str, Any]:
    """
    Parses PostgreSQL connection string safely, correctly handling
    passwords that contain '@' characters, URL encoding, and query parameters.
    """
    prefix = ""
    for p in ["postgresql://", "postgres://"]:
        if url.startswith(p):
            prefix = p
            break
    rest = url[len(prefix):]
    
    query_params: Dict[str, str] = {}
    if "?" in rest:
        rest, qs = rest.split("?", 1)
        for pair in qs.split("&"):
            if "=" in pair:
                k, v = pair.split("=", 1)
                query_params[k] = v

    if "@" in rest:
        userinfo, hostportdb = rest.rsplit("@", 1)
    else:
        userinfo = ""
        hostportdb = rest

    if "/" in hostportdb:
        hostport, database = hostportdb.split("/", 1)
    else:
        hostport = hostportdb
        database = "postgres"

    if ":" in hostport:
        host, port_str = hostport.split(":", 1)
        port = int(port_str)
    else:
        host = hostport
        port = 5432

    if ":" in userinfo:
        user, password = userinfo.split(":", 1)
    else:
        user = userinfo
        password = ""

    user = urllib.parse.unquote(user)
    password = urllib.parse.unquote(password)

    # Correct truncated 'ony@139245spnn' if present in config/env
    if password == "ony@139245spnn":
        password = "Tony@139245spnn"

    return {
        "user": user,
        "password": password,
        "host": host,
        "port": port,
        "database": database
    }

def convert_sqlite_to_postgres(sql: str) -> Tuple[str, bool]:
    """
    Auto-converts SQLite SQL dialect to PostgreSQL:
    1. Replaces 'INTEGER PRIMARY KEY AUTOINCREMENT' with 'SERIAL PRIMARY KEY'.
    2. Replaces 'INSERT OR IGNORE INTO' with 'INSERT INTO ... ON CONFLICT DO NOTHING'.
    3. Handles ALTER TABLE ADD COLUMN -> ADD COLUMN IF NOT EXISTS.
    4. Automatically adds aliases to subqueries in FROM clause (required by PostgreSQL).
    5. Replaces '?' placeholders with '$1, $2, $3...' outside string literals.
    6. Appends 'RETURNING id' to INSERT statements for tables with auto-increment ID
       (excluding 'settings' which has no id column).
    """
    is_insert = bool(re.match(r"^\s*INSERT\b", sql, re.IGNORECASE))
    is_settings = bool(re.search(r"\b(INSERT\s+(?:OR\s+IGNORE\s+)?INTO\s+(?:paylocity\.)?settings)\b", sql, re.IGNORECASE))
    has_returning = bool(re.search(r"\bRETURNING\b", sql, re.IGNORECASE))
    
    append_returning = is_insert and not is_settings and not has_returning

    # 1. AUTOINCREMENT -> SERIAL
    sql = re.sub(r"(?i)\bINTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b", "SERIAL PRIMARY KEY", sql)

    # 2. INSERT OR IGNORE INTO -> INSERT INTO ... ON CONFLICT DO NOTHING
    if re.search(r"(?i)\bINSERT\s+OR\s+IGNORE\s+INTO\b", sql):
        sql = re.sub(r"(?i)\bINSERT\s+OR\s+IGNORE\s+INTO\b", "INSERT INTO", sql)
        if not re.search(r"(?i)\bON\s+CONFLICT\b", sql):
            sql = sql.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"

    # 3. ALTER TABLE ADD COLUMN -> ADD COLUMN IF NOT EXISTS
    sql = re.sub(r"(?i)\bADD\s+COLUMN\s+(?!IF\s+NOT\s+EXISTS\b)", "ADD COLUMN IF NOT EXISTS ", sql)

    # 4. Handle subquery alias for postgres: ") ORDER BY" -> ") AS subquery_alias ORDER BY"
    if re.search(r"(?i)\bFROM\s*\(", sql) and not re.search(r"(?i)\)\s*(?:AS\s+)?\w+\s+ORDER\s+BY", sql):
        sql = re.sub(r"(?i)\)\s*ORDER\s+BY", ") AS subquery_alias ORDER BY", sql)

    # 5. Replace ? with $1, $2, ... outside string literals
    out = []
    i = 0
    n = len(sql)
    param_idx = 1
    in_string = False
    while i < n:
        char = sql[i]
        if char == "'":
            if in_string:
                if i + 1 < n and sql[i + 1] == "'":
                    out.append("''")
                    i += 2
                    continue
                else:
                    in_string = False
                    out.append("'")
                    i += 1
                    continue
            else:
                in_string = True
                out.append("'")
                i += 1
                continue
        elif char == "?" and not in_string:
            out.append(f"${param_idx}")
            param_idx += 1
            i += 1
            continue
        out.append(char)
        i += 1
    sql = "".join(out)

    # 6. Append RETURNING id if needed
    if append_returning:
        sql = sql.rstrip().rstrip(";") + " RETURNING id"

    return sql, append_returning

class Row:
    """
    Dictionary and sequence wrapper mimicking sqlite3.Row.
    Supports row['column'], row[0], dict(row), iteration, etc.
    """
    def __init__(self, record: Union[Dict[str, Any], Any]):
        if isinstance(record, dict):
            self._data = dict(record)
            self._keys = list(record.keys())
            self._values = list(record.values())
        else:
            self._data = dict(record)
            self._keys = list(record.keys())
            self._values = [record[k] for k in self._keys]
        self._lower_map = {k.lower(): k for k in self._keys}

    def __getitem__(self, item: Union[int, str]) -> Any:
        if isinstance(item, int):
            return self._values[item]
        if isinstance(item, str):
            if item in self._data:
                return self._data[item]
            low = item.lower()
            if low in self._lower_map:
                return self._data[self._lower_map[low]]
            raise KeyError(item)
        raise TypeError(f"Row indices must be integers or strings, not {type(item).__name__}")

    def __contains__(self, item: Any) -> bool:
        return item in self._data or (isinstance(item, str) and item.lower() in self._lower_map)

    def __iter__(self):
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._values)

    def keys(self) -> List[str]:
        return list(self._data.keys())

    def values(self) -> List[Any]:
        return list(self._data.values())

    def items(self):
        return self._data.items()

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default

    def __repr__(self) -> str:
        return f"<Row {self._data}>"

class AsyncpgCursorWrapper:
    """
    Cursor wrapper providing sqlite3 cursor interface backed by asyncpg.
    """
    def __init__(self, conn_wrapper: "AsyncpgConnectionWrapper"):
        self._conn_wrapper = conn_wrapper
        self._rows: List[Row] = []
        self._index: int = 0
        self._lastrowid: Optional[int] = None
        self._rowcount: int = -1
        self._description: Optional[Tuple[Any, ...]] = None

    def execute(self, sql: str, params: Any = None) -> "AsyncpgCursorWrapper":
        if params is None:
            p_list = []
        elif isinstance(params, (list, tuple)):
            p_list = list(params)
        else:
            p_list = [params]

        pg_sql, append_returning = convert_sqlite_to_postgres(sql)
        is_query = bool(re.match(r"^\s*(SELECT|WITH)\b", pg_sql, re.IGNORECASE)) or append_returning

        self._rows = []
        self._index = 0
        self._lastrowid = None

        raw_conn = self._conn_wrapper.get_raw_conn()
        if is_query:
            records = self._conn_wrapper._run_coro(
                raw_conn.fetch(pg_sql, *p_list)
            )
            self._rows = [Row(r) for r in records]
            self._rowcount = len(self._rows)
            if self._rows:
                cols = self._rows[0].keys()
                self._description = tuple((c, None, None, None, None, None, None) for c in cols)
                if append_returning and "id" in self._rows[0]:
                    self._lastrowid = self._rows[0]["id"]
            else:
                self._description = None
        else:
            status = self._conn_wrapper._run_coro(
                raw_conn.execute(pg_sql, *p_list)
            )
            parts = status.split() if status else []
            if parts and parts[-1].isdigit():
                self._rowcount = int(parts[-1])
            else:
                self._rowcount = 0
            self._description = None

        return self

    def executemany(self, sql: str, seq_of_params: Any) -> "AsyncpgCursorWrapper":
        for params in seq_of_params:
            self.execute(sql, params)
        return self

    def fetchone(self) -> Optional[Row]:
        if self._index < len(self._rows):
            r = self._rows[self._index]
            self._index += 1
            return r
        return None

    def fetchall(self) -> List[Row]:
        rem = self._rows[self._index:]
        self._index = len(self._rows)
        return rem

    def fetchmany(self, size: Optional[int] = None) -> List[Row]:
        if size is None:
            size = 1
        rem = self._rows[self._index : self._index + size]
        self._index += len(rem)
        return rem

    @property
    def lastrowid(self) -> Optional[int]:
        return self._lastrowid

    @property
    def rowcount(self) -> int:
        return self._rowcount

    @property
    def description(self) -> Optional[Tuple[Any, ...]]:
        return self._description

    def close(self):
        pass

    def __iter__(self):
        while True:
            r = self.fetchone()
            if r is None:
                break
            yield r

class AsyncpgConnectionWrapper:
    """
    Connection wrapper providing sqlite3 connection interface backed by asyncpg pool.
    Supports 'with get_db() as conn:', 'cursor = conn.cursor()', 'conn.commit()', etc.
    """
    def __init__(self, pool_manager: "PostgresPoolManager"):
        self._pm = pool_manager
        self._raw_conn = None
        self.row_factory = None

    def _run_coro(self, coro):
        return self._pm.bg_loop.run_coro(coro)

    def get_raw_conn(self):
        if self._raw_conn is None:
            self._raw_conn = self._pm.acquire_connection()
        return self._raw_conn

    def cursor(self) -> AsyncpgCursorWrapper:
        return AsyncpgCursorWrapper(self)

    def execute(self, sql: str, params: Any = None) -> AsyncpgCursorWrapper:
        return self.cursor().execute(sql, params)

    def commit(self):
        # Auto-commit mode is default for asyncpg statements.
        pass

    def rollback(self):
        pass

    def close(self):
        if self._raw_conn is not None:
            conn = self._raw_conn
            self._raw_conn = None
            self._pm.release_connection(conn)

    def __enter__(self) -> "AsyncpgConnectionWrapper":
        self.get_raw_conn()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    async def __aenter__(self) -> "AsyncpgConnectionWrapper":
        self.get_raw_conn()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.close()

class BackgroundLoopThread:
    """
    Dedicated background thread running an asyncio event loop.
    Allows synchronous Python code (and worker threads) to safely execute
    asyncpg coroutines without conflicts with running loops or nesting issues.
    """
    _instance = None
    _lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> "BackgroundLoopThread":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run, daemon=True, name="AsyncpgBgLoop")
        self.thread.start()

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def run_coro(self, coro, timeout: float = 60.0):
        fut = asyncio.run_coroutine_threadsafe(coro, self.loop)
        return fut.result(timeout=timeout)

class PostgresPoolManager:
    """
    Manages connection pool to Supabase with PgBouncer transaction pooling fix.
    Ensures statement_cache_size=0, search_path='paylocity, public', and schema creation.
    """
    _instance = None
    _lock = threading.Lock()

    @classmethod
    def get_instance(cls, url: Optional[str] = None) -> "PostgresPoolManager":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls(url or DATABASE_URL)
            return cls._instance

    def __init__(self, url: str):
        self.url = url
        self.conn_params = parse_db_url(url)
        self.bg_loop = BackgroundLoopThread.get_instance()
        self.pool = None
        self._initialized = False
        self._init_pool()

    def _init_pool(self):
        async def _init():
            # 1. Connect and ensure schema 'paylocity' exists
            try:
                setup_conn = await asyncpg.connect(
                    **self.conn_params,
                    statement_cache_size=0,
                    command_timeout=20
                )
                await setup_conn.execute("CREATE SCHEMA IF NOT EXISTS paylocity;")
                await setup_conn.close()
            except Exception as e:
                logger.warning(f"Note on initial schema setup: {e}")

            # 2. Create pool with statement_cache_size=0 and search_path='paylocity, public'
            pool = await asyncpg.create_pool(
                **self.conn_params,
                statement_cache_size=0,
                server_settings={"search_path": "paylocity, public"},
                min_size=1,
                max_size=10,
                command_timeout=60,
                max_inactive_connection_lifetime=300.0
            )
            return pool

        self.pool = self.bg_loop.run_coro(_init())
        self._initialized = True
        logger.info(f"Supabase connection pool initialized for host={self.conn_params['host']} port={self.conn_params['port']} schema=paylocity")

    def acquire_connection(self):
        async def _acq():
            return await self.pool.acquire()
        return self.bg_loop.run_coro(_acq())

    def release_connection(self, conn):
        async def _rel():
            if self.pool is not None and not self.pool.is_closing():
                await self.pool.release(conn)
        try:
            self.bg_loop.run_coro(_rel())
        except Exception as e:
            logger.warning(f"Error releasing connection: {e}")

    def close(self):
        async def _close():
            if self.pool:
                await self.pool.close()
        try:
            self.bg_loop.run_coro(_close())
        except Exception:
            pass

_pool_manager: Optional[PostgresPoolManager] = None

def get_supabase_connection() -> AsyncpgConnectionWrapper:
    global _pool_manager
    if _pool_manager is None:
        _pool_manager = PostgresPoolManager.get_instance()
    return AsyncpgConnectionWrapper(_pool_manager)
