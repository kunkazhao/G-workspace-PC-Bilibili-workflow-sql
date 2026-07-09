import sqlite3
from pathlib import Path


def main() -> None:
    conn = sqlite3.connect(Path("data") / "bworkflow.db")
    for name, sql in conn.execute(
        "select name, sql from sqlite_master where type='table' order by name"
    ):
        print(f"--- {name}")
        print((sql or "")[:1200])


if __name__ == "__main__":
    main()
