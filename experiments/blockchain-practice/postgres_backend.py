"""One PostgreSQL connection and deployment fence shared by both team engines.

Reconnecting never takes ownership back from a newer deployment. Database driver
messages are deliberately not exposed because they can contain connection data.
"""

from contextlib import contextmanager
import re
import secrets
import threading

import psycopg
from psycopg.rows import dict_row
from psycopg.pq import TransactionStatus


class PostgresBackendError(RuntimeError):
    """A sanitized connection, transaction or schema error."""


class StaleWriterError(PostgresBackendError):
    """An older application process must not overwrite the current deployment."""


_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,62}\Z", re.ASCII)
_PREFIX = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,52}\Z", re.ASCII)
_KINDS = frozenset(("control", "basic", "education"))


def _quoted(value):
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError("Invalid PostgreSQL identifier")
    return '"' + value + '"'


class PostgresBackend:
    def __init__(self, url, prefix="team", *, defer_claim=False):
        if not isinstance(prefix, str) or not _PREFIX.fullmatch(prefix):
            raise ValueError("Invalid PostgreSQL schema prefix")
        if not isinstance(url, str) or not url.strip():
            raise ValueError("A PostgreSQL connection URL is required")
        self.prefix = prefix
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.RLock()
        self._url = url
        self._connection = None
        self._closed = False
        self._active = False
        self._bootstrapping = True
        try:
            self._bootstrap()
            if not defer_claim:
                self.activate()
        except Exception:
            self.close()
            raise

    def __repr__(self):
        return f"PostgresBackend(prefix={self.prefix!r}, closed={self._closed})"

    def schema_name(self, kind):
        if not isinstance(kind, str) or kind not in _KINDS:
            raise ValueError("Unknown PostgreSQL schema kind")
        return self.prefix + "_" + kind

    def schema(self, kind):
        return _quoted(self.schema_name(kind))

    def table(self, kind, table):
        return self.schema(kind) + "." + _quoted(table)

    def _get_connection(self):
        if self._closed:
            raise PostgresBackendError("PostgreSQL backend is closed")
        if self._connection is None or self._connection.closed or self._connection.broken:
            self._discard_connection()
            try:
                self._connection = psycopg.connect(
                    self._url, autocommit=True, row_factory=dict_row,
                    connect_timeout=10, prepare_threshold=None,
                )
            except (psycopg.Error, OSError, ValueError):
                raise PostgresBackendError("PostgreSQL connection failed; check availability and configuration") from None
        return self._connection

    def _discard_connection(self):
        connection, self._connection = self._connection, None
        if connection is not None:
            try:
                connection.close()
            except (psycopg.Error, OSError):
                pass

    def validate_table(self, connection, kind, table, column_types, *,
                       primary_key=("id",), nullable=(), identity_columns=()):
        """Return False when absent; otherwise validate shape before any table DDL.

        column_types uses PostgreSQL udt names such as text, int4 and int8.
        An identity or serial column is accepted when explicitly requested.
        """
        _quoted(table)
        name = self.schema_name(kind)
        relation = connection.execute(
            "SELECT c.relkind FROM pg_catalog.pg_class c "
            "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname=%s AND c.relname=%s",
            (name, table),
        ).fetchone()
        if relation is None:
            return False
        if relation["relkind"] != "r":
            raise PostgresBackendError("PostgreSQL relation is not an ordinary application table; existing data was not changed")
        columns = connection.execute(
            "SELECT column_name,udt_name,is_nullable,domain_name,is_identity,column_default,is_generated "
            "FROM information_schema.columns WHERE table_schema=%s AND table_name=%s",
            (name, table),
        ).fetchall()
        fields = {row["column_name"]: row for row in columns}
        allowed_nulls = set(nullable)
        valid = set(fields) == set(column_types)
        if valid:
            valid = all(
                fields[key]["udt_name"] == expected
                and fields[key]["domain_name"] is None
                and fields[key]["is_generated"] == "NEVER"
                and (fields[key]["is_nullable"] == "YES") == (key in allowed_nulls)
                for key, expected in column_types.items()
            )
        keys = connection.execute(
            "SELECT k.column_name FROM information_schema.table_constraints c "
            "JOIN information_schema.key_column_usage k "
            "ON c.constraint_catalog=k.constraint_catalog AND c.constraint_schema=k.constraint_schema "
            "AND c.constraint_name=k.constraint_name AND c.table_schema=k.table_schema AND c.table_name=k.table_name "
            "WHERE c.table_schema=%s AND c.table_name=%s AND c.constraint_type='PRIMARY KEY' "
            "ORDER BY k.ordinal_position", (name, table),
        ).fetchall()
        valid = valid and tuple(row["column_name"] for row in keys) == tuple(primary_key)
        for key in identity_columns:
            field = fields.get(key, {})
            valid = valid and (field.get("is_identity") == "YES"
                               or str(field.get("column_default", "")).startswith("nextval("))
        if not valid:
            raise PostgresBackendError("PostgreSQL table schema does not match this application; existing data was not changed")
        return True

    def _bootstrap(self):
        with self.lock:
            connection = self._get_connection()
            try:
                with connection.transaction():
                    for kind in sorted(_KINDS):
                        connection.execute("CREATE SCHEMA IF NOT EXISTS " + self.schema(kind))
                    deployment = self.table("control", "deployment")
                    expected = {"id": "int4", "writer_token": "text"}
                    self.validate_table(connection, "control", "deployment", expected)
                    connection.execute(
                        f"CREATE TABLE IF NOT EXISTS {deployment} ("
                        "id INTEGER PRIMARY KEY CHECK (id=1), writer_token TEXT NOT NULL)"
                    )
                    self.validate_table(connection, "control", "deployment", expected)
            except psycopg.Error:
                raise PostgresBackendError("PostgreSQL initialization failed; check permissions and database availability") from None

    def activate(self):
        """Claim the writer only after all application initialization succeeded.

        Calling this again on an old activated backend never steals ownership
        back. Deferred bootstrapping is internal startup work, not a serving mode.
        """
        with self.lock:
            connection = self._get_connection()
            if self._active:
                return
            try:
                with connection.transaction():
                    connection.execute(
                        f"INSERT INTO {self.table('control', 'deployment')} (id,writer_token) VALUES (1,%s) "
                        "ON CONFLICT(id) DO UPDATE SET writer_token=excluded.writer_token",
                        (self.token,),
                    )
            except (psycopg.Error, OSError):
                raise PostgresBackendError("PostgreSQL writer activation failed; check database availability") from None
            self._active = True
            self._bootstrapping = False

    @contextmanager
    def transaction(self, write=False):
        with self.lock:
            connection = self._get_connection()
            top_level = connection.info.transaction_status == TransactionStatus.IDLE
            try:
                with connection.transaction():
                    if not write and top_level:
                        connection.execute("SET TRANSACTION READ ONLY")
                    if write and not self._bootstrapping:
                        row = connection.execute(
                            f"SELECT writer_token FROM {self.table('control', 'deployment')} WHERE id=1 FOR UPDATE"
                        ).fetchone()
                        if row is None or row["writer_token"] != self.token:
                            raise StaleWriterError("previous deployment is no longer allowed to write")
                    yield connection
            except (psycopg.OperationalError, psycopg.InterfaceError, OSError):
                self._discard_connection()
                raise PostgresBackendError("PostgreSQL transaction failed; check database availability and limits") from None
            except psycopg.Error:
                raise PostgresBackendError("PostgreSQL transaction failed; the operation was not acknowledged") from None

    def close(self):
        with self.lock:
            if self._closed:
                return
            self._closed = True
            self._discard_connection()
