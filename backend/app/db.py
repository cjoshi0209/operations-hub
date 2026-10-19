"""Engine / session setup.

Two engines point at the same database:

* ``read_engine``  - plain deferred transactions (WAL means readers never block).
* ``write_engine`` - on SQLite every transaction starts with ``BEGIN IMMEDIATE`` so writers queue on the
  file lock instead of failing when a read transaction tries to upgrade to a write (SQLITE_BUSY_SNAPSHOT).

On Postgres both engines are identical; the guarded UPDATEs and unique constraints carry the guarantees.
"""
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


def _configure_sqlite(engine: Engine, immediate: bool) -> None:
    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_connection, _record):
        # Take transaction control away from pysqlite so BEGIN is emitted exactly when we say.
        dbapi_connection.isolation_level = None
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA busy_timeout=30000")
        cur.close()

    @event.listens_for(engine, "begin")
    def _on_begin(conn):
        conn.exec_driver_sql("BEGIN IMMEDIATE" if immediate else "BEGIN")


class Database:
    def __init__(self, url: str):
        self.url = url
        is_sqlite = url.startswith("sqlite")
        kwargs = {"connect_args": {"check_same_thread": False}} if is_sqlite else {"pool_pre_ping": True}
        self.read_engine = create_engine(url, **kwargs)
        self.write_engine = create_engine(url, **kwargs)
        if is_sqlite:
            _configure_sqlite(self.read_engine, immediate=False)
            _configure_sqlite(self.write_engine, immediate=True)
        self._read = sessionmaker(self.read_engine, expire_on_commit=False, autoflush=False)
        self._write = sessionmaker(self.write_engine, expire_on_commit=False, autoflush=False)

    @property
    def is_sqlite(self) -> bool:
        return self.url.startswith("sqlite")

    def read_session(self) -> Session:
        return self._read()

    def write_session(self) -> Session:
        return self._write()

    def create_all(self) -> None:
        from . import models  # noqa: F401  (register tables)

        Base.metadata.create_all(self.write_engine)

    def dispose(self) -> None:
        self.read_engine.dispose()
        self.write_engine.dispose()
