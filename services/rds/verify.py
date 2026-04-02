#!/usr/bin/env python3
"""RDS verify: check validation row on target DB."""

import argparse, hashlib, json, sys
from services.shared.utils import load_config

VALIDATION_TABLE = "_migration_validation"


def _get_db_connection(db_url):
    from urllib.parse import urlparse
    parsed = urlparse(db_url)
    if parsed.scheme in ("postgres", "postgresql"):
        import psycopg2
        return psycopg2.connect(host=parsed.hostname, port=parsed.port or 5432,
                                 user=parsed.username, password=parsed.password,
                                 dbname=parsed.path.lstrip("/")), "postgres"
    elif parsed.scheme == "mysql":
        import pymysql
        return pymysql.connect(host=parsed.hostname, port=parsed.port or 3306,
                                user=parsed.username, password=parsed.password,
                                database=parsed.path.lstrip("/")), "mysql"
    raise ValueError(f"Unsupported DB scheme: {parsed.scheme}")


def verify_rds(db_url, expected_token):
    conn, dialect = _get_db_connection(db_url)
    cur = conn.cursor()
    expected_checksum = hashlib.sha256(expected_token.encode()).hexdigest()

    print(f"\n🔍 Verifying RDS validation data ({dialect})...")
    try:
        cur.execute(f"SELECT migration_token, checksum, test_data FROM {VALIDATION_TABLE} WHERE migration_token = %s",
                    (expected_token,))
        row = cur.fetchone()
    except Exception as e:
        print(f"  ❌ Query failed: {e}")
        return False
    finally:
        cur.close()
        conn.close()

    if not row:
        print(f"  ❌ Token not found in {VALIDATION_TABLE}")
        return False

    if row[0] == expected_token and row[1] == expected_checksum:
        data = json.loads(row[2]) if isinstance(row[2], str) else row[2]
        print(f"  ✅ Token matched: {row[0]}")
        print(f"  ✅ Checksum matched")
        print(f"  ✅ Test data rows: {len(data.get('sample_rows', []))}")
        return True
    else:
        print(f"  ❌ Checksum mismatch")
        return False


def main():
    parser = argparse.ArgumentParser(description="RDS verify: check validation row on target")
    parser.add_argument("--db-url", required=True, help="Target DB URL")
    parser.add_argument("--token", required=True, help="Token from prepare step")
    args = parser.parse_args()

    ok = verify_rds(args.db_url, args.token)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
