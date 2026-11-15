"""Initialize the team's mounted disk, then exec the server without privileges.

Only /data (or an explicitly configured TEAM_DATA_DIR), configured SQLite files,
their sidecars, and the backup directory are touched. No recursive chown occurs.
"""
import os
from pathlib import Path
import sys
import tempfile
from urllib.parse import urlsplit

APP_UID = 10001
APP_GID = 10001


def storage_paths(environ):
    root = Path(environ.get("TEAM_DATA_DIR", "/data")).expanduser()
    if not root.is_absolute():
        raise ValueError("TEAM_DATA_DIR must be an absolute directory")
    root = root.resolve()
    if root == Path(root.anchor):
        raise ValueError("TEAM_DATA_DIR must not be a filesystem root")

    def beneath(value, label, *, directory=False):
        path = Path(value).expanduser()
        if not path.is_absolute():
            raise ValueError(f"{label} must be an absolute path under TEAM_DATA_DIR")
        path = path.resolve()
        if not path.is_relative_to(root) or (path == root and not directory):
            raise ValueError(f"{label} must stay under TEAM_DATA_DIR")
        return path

    databases = (
        beneath(environ.get("KAKAO_PRACTICE_DB", str(root / "basic.sqlite3")), "KAKAO_PRACTICE_DB"),
        beneath(environ.get("TEAM_EDUCATION_DB", str(root / "education.sqlite3")), "TEAM_EDUCATION_DB"),
    )
    if databases[0] == databases[1]:
        raise ValueError("The basic and education databases must be different files")
    backups = beneath(environ.get("TEAM_BACKUP_DIR", str(root / "backups")), "TEAM_BACKUP_DIR", directory=True)
    return root, databases, backups


def prepare_storage(root, databases, backups, *, privileged):
    directories = {root}
    for destination in (backups, *(path.parent for path in databases)):
        directories.add(destination)
        directories.update(parent for parent in destination.parents if parent.is_relative_to(root))
    for directory in sorted(directories, key=lambda path: len(path.parts)):
        directory.mkdir(parents=True, exist_ok=True)
        if privileged:
            os.chown(directory, APP_UID, APP_GID, follow_symlinks=False)
            directory.chmod(0o700)
    if privileged:
        for database in databases:
            for suffix in ("", "-wal", "-shm", "-journal"):
                path = Path(str(database) + suffix)
                # Refuse sidecar symlinks rather than changing another target.
                if path.is_symlink():
                    raise ValueError("SQLite data files must not be symbolic links")
                if path.exists():
                    if not path.is_file():
                        raise ValueError("SQLite data paths must be regular files")
                    os.chown(path, APP_UID, APP_GID, follow_symlinks=False)
                    path.chmod(0o600)


def verify_writable(root, databases, backups):
    for directory in {root, backups, *(path.parent for path in databases)}:
        with tempfile.TemporaryFile(prefix=".team-write-probe-", dir=directory):
            pass
    for database in databases:
        if database.exists() and not os.access(database, os.R_OK | os.W_OK):
            raise PermissionError("The application user cannot read/write a configured database")


def main(argv=None, environ=None):
    environ = os.environ if environ is None else environ
    if not hasattr(os, "geteuid"):
        raise RuntimeError("The deployment entrypoint requires a POSIX container")
    secret = environ.get("TEAM_TEACHER_SECRET", "")
    if not 32 <= len(secret) <= 100:
        raise ValueError("Set TEAM_TEACHER_SECRET to a persistent random value of 32–100 characters")
    postgres = bool(environ.get("TEAM_DATABASE_URL"))
    if postgres:
        parsed = urlsplit(environ["TEAM_DATABASE_URL"])
        if parsed.scheme not in ("postgres", "postgresql") or not parsed.hostname:
            raise ValueError("TEAM_DATABASE_URL must be a PostgreSQL connection URL")
    else:
        root, databases, backups = storage_paths(environ)
    # SQLite creates its main file and later sidecars after this entrypoint runs.
    os.umask(0o077)
    privileged = os.geteuid() == 0
    if not postgres:
        prepare_storage(root, databases, backups, privileged=privileged)
    if privileged:
        os.setgroups([])
        os.setgid(APP_GID)
        os.setuid(APP_UID)
    elif os.geteuid() != APP_UID:
        raise PermissionError("Run the entrypoint as root for disk setup, or as UID 10001")
    environ["HOME"] = "/home/teamapp"
    environ["USER"] = environ["LOGNAME"] = "teamapp"
    if not postgres:
        environ["KAKAO_PRACTICE_DB"], environ["TEAM_EDUCATION_DB"] = map(str, databases)
        verify_writable(root, databases, backups)
    command = list(sys.argv[1:] if argv is None else argv)
    if not command:
        command = ["gunicorn", "--config", "gunicorn.conf.py", "app:app"]
    os.execvpe(command[0], command, environ)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Team deployment startup failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
