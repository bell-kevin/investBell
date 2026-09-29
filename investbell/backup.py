"""Stage a consistent copy of the data directory for an off-machine backup.

deploy/investbell-backup.service runs this on the host with python3, then sends
the staging directory to the backup machine with restic. SQLite databases are
copied with SQLite's online backup, so the scheduler and the paper runner can
keep writing during the copy; their journal and WAL files are left out because
the copy already holds everything committed. Every other file is copied as it
is, apart from the top-level dependency cache, which rebuilds itself. Only the
standard library is used.
"""

import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sqlite3


SKIPPED_DIRECTORIES = ("cache",)
DATABASE_SUFFIX = ".sqlite3"
DATABASE_COMPANIONS = ("-journal", "-wal", "-shm")


def is_database_file(name):
    """Return True for a SQLite database or one of its journal files."""
    return any(name.endswith(DATABASE_SUFFIX + companion) for companion in ("",) + DATABASE_COMPANIONS)


def copy_database(source, destination):
    """Copy a SQLite database that may be in use, through the online backup API."""
    reader = sqlite3.connect(Path(source).resolve().as_uri() + "?mode=ro", uri=True)
    with closing(reader), closing(sqlite3.connect(destination)) as writer:
        reader.backup(writer)


def stage(data_dir, staging_dir):
    """Replace staging_dir with a copy of data_dir; return the databases' relative paths."""
    data_dir, staging_dir = Path(data_dir), Path(staging_dir)
    if not data_dir.is_dir():
        raise FileNotFoundError(f"{data_dir} is not a directory.")
    databases = []

    def skip(directory, names):
        directory = Path(directory)
        skipped = []
        for name in names:
            path = directory / name
            if directory == data_dir and name in SKIPPED_DIRECTORIES and path.is_dir():
                skipped.append(name)
            elif path.is_file() and is_database_file(name):
                skipped.append(name)
                if name.endswith(DATABASE_SUFFIX):
                    databases.append(path.relative_to(data_dir))
        return skipped

    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    shutil.copytree(data_dir, staging_dir, ignore=skip)
    # A data directory without a database is the wrong directory or a lost one;
    # fail rather than record an empty backup.
    if not databases:
        raise FileNotFoundError(f"No {DATABASE_SUFFIX} database in {data_dir}.")
    databases.sort()
    for relative in databases:
        copy_database(data_dir / relative, staging_dir / relative)
    return databases


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("data_dir", type=Path, help="the application's data directory")
    parser.add_argument("staging_dir", type=Path, help="directory to replace with the copy")
    args = parser.parse_args(argv)
    databases = stage(args.data_dir, args.staging_dir)
    print(json.dumps({"time": datetime.now(timezone.utc).isoformat(), "event": "backup_staged",
                      "databases": [str(path) for path in databases]}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
