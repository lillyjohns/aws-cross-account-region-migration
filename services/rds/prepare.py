#!/usr/bin/env python3
"""RDS prepare: seed validation row + fingerprint."""

import argparse, hashlib, json, sys, uuid, boto3
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


def seed_rds(db_url):
    conn, dialect = _get_db_connection(db_url)
    cur = conn.cursor()
    token = str(uuid.uuid4())
    checksum = hashlib.sha256(token.encode()).hexdigest()

    print(f"\n🌱 Seeding RDS validation data ({dialect})...")

    if dialect == "postgres":
        cur.execute(f"""CREATE TABLE IF NOT EXISTS {VALIDATION_TABLE} (
            id SERIAL PRIMARY KEY, migration_token VARCHAR(64) NOT NULL,
            checksum VARCHAR(128) NOT NULL, created_at TIMESTAMP DEFAULT NOW(), test_data JSONB)""")
    else:
        cur.execute(f"""CREATE TABLE IF NOT EXISTS {VALIDATION_TABLE} (
            id INT AUTO_INCREMENT PRIMARY KEY, migration_token VARCHAR(64) NOT NULL,
            checksum VARCHAR(128) NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, test_data JSON)""")

    test_data = json.dumps({"sample_rows": [{"name": "Alice", "value": 42}, {"name": "Bob", "value": 99}],
                            "purpose": "migration_validation"})
    cur.execute(f"INSERT INTO {VALIDATION_TABLE} (migration_token, checksum, test_data) VALUES (%s, %s, %s)",
                (token, checksum, test_data))
    conn.commit()
    cur.close()
    conn.close()

    print(f"  ✅ Token: {token}")
    print(f"  ✅ Checksum: {checksum}")
    return {"token": token}


def fingerprint(session, db_instance_id):
    rds = session.client("rds")
    db = rds.describe_db_instances(DBInstanceIdentifier=db_instance_id)["DBInstances"][0]
    fp = {"db_instance_id": db_instance_id, "engine": db["Engine"], "engine_version": db["EngineVersion"],
          "instance_class": db["DBInstanceClass"], "storage_gb": db["AllocatedStorage"],
          "encrypted": db["StorageEncrypted"]}
    print(f"\n🔍 RDS: {db_instance_id}")
    print(f"  Engine: {fp['engine']} {fp['engine_version']} | Storage: {fp['storage_gb']} GB")
    return fp


def main():
    parser = argparse.ArgumentParser(description="RDS prepare: seed validation row + fingerprint")
    parser.add_argument("-c", "--config", default="scripts/config.yaml")
    parser.add_argument("--db-url", required=True, help="postgres://user:pass@host:port/db")
    args = parser.parse_args()

    cfg = load_config(args.config)
    result = seed_rds(args.db_url)
    session = boto3.Session(profile_name=cfg["source"]["profile"], region_name=cfg["source"]["region"])
    for db in cfg["rds"]["instances"]:
        fingerprint(session, db["db_instance_id"])
    print(f"\n💾 Save this token for verification: {result['token']}")


if __name__ == "__main__":
    main()
