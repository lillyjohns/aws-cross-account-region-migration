#!/usr/bin/env python3
"""Seed test data before migration, verify it exists after.

EC2: Creates a marker file via SSM Run Command, verifies it on target.
RDS: Inserts a validation record via direct DB connection, verifies on target.

Requires:
  - EC2: SSM Agent running on instances, IAM role with ssm:SendCommand
  - RDS: Network access + credentials (via --db-url or env DB_URL)
  - pip install pymysql psycopg2-binary (for RDS)
"""

import argparse, hashlib, json, sys, time, uuid, yaml, boto3

MARKER_PATH = "/tmp/migration-marker.json"
VALIDATION_TABLE = "_migration_validation"


def load_config(path):
    with open(path) as f:
        return yaml.safe_load(f)


# ── EC2 (via SSM) ───────────────────────────────────────────────────

def _ssm_run(ssm, instance_id, commands, label="command"):
    resp = ssm.send_command(
        InstanceIds=[instance_id],
        DocumentName="AWS-RunShellCommand",
        Parameters={"commands": commands},
        Comment=f"migration-{label}",
    )
    cmd_id = resp["Command"]["CommandId"]

    for _ in range(30):
        time.sleep(2)
        result = ssm.get_command_invocation(CommandId=cmd_id, InstanceId=instance_id)
        if result["Status"] in ("Success", "Failed", "TimedOut", "Cancelled"):
            return result
    raise TimeoutError(f"SSM command timed out on {instance_id}")


def seed_ec2(session, instance_id):
    ssm = session.client("ssm")
    token = str(uuid.uuid4())
    marker = json.dumps({
        "migration_token": token,
        "source_instance": instance_id,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "checksum": hashlib.sha256(token.encode()).hexdigest(),
    })

    print(f"\n🌱 Seeding EC2 marker on {instance_id}...")
    result = _ssm_run(ssm, instance_id, [
        f"cat > {MARKER_PATH} << 'MARKER_EOF'\n{marker}\nMARKER_EOF",
        f"cat {MARKER_PATH}",
    ], label="seed")

    if result["Status"] != "Success":
        print(f"  ❌ SSM failed: {result.get('StandardErrorContent', 'unknown')}")
        sys.exit(1)

    print(f"  ✅ Marker file created: {MARKER_PATH}")
    print(f"  Token: {token}")
    return {"instance_id": instance_id, "token": token, "marker_path": MARKER_PATH}


def verify_ec2(session, instance_id, expected_token):
    ssm = session.client("ssm")

    print(f"\n🔍 Verifying EC2 marker on {instance_id}...")
    result = _ssm_run(ssm, instance_id, [f"cat {MARKER_PATH}"], label="verify")

    if result["Status"] != "Success":
        print(f"  ❌ Marker file not found or SSM failed")
        print(f"     {result.get('StandardErrorContent', '')}")
        return False

    try:
        marker = json.loads(result["StandardOutputContent"].strip())
    except json.JSONDecodeError:
        print(f"  ❌ Marker file exists but content is invalid")
        return False

    found_token = marker.get("migration_token")
    expected_checksum = hashlib.sha256(expected_token.encode()).hexdigest()

    if found_token == expected_token and marker.get("checksum") == expected_checksum:
        print(f"  ✅ Token matched: {found_token}")
        print(f"  ✅ Checksum matched: {marker['checksum']}")
        print(f"  ✅ Source instance: {marker['source_instance']}")
        return True
    else:
        print(f"  ❌ Token mismatch: expected={expected_token}, found={found_token}")
        return False


# ── RDS (via DB connection) ─────────────────────────────────────────

def _get_db_connection(db_url):
    """Parse DB URL and return connection. Supports postgres:// and mysql://"""
    from urllib.parse import urlparse
    parsed = urlparse(db_url)

    if parsed.scheme in ("postgres", "postgresql"):
        import psycopg2
        return psycopg2.connect(
            host=parsed.hostname, port=parsed.port or 5432,
            user=parsed.username, password=parsed.password,
            dbname=parsed.path.lstrip("/"),
        ), "postgres"
    elif parsed.scheme == "mysql":
        import pymysql
        return pymysql.connect(
            host=parsed.hostname, port=parsed.port or 3306,
            user=parsed.username, password=parsed.password,
            database=parsed.path.lstrip("/"),
        ), "mysql"
    else:
        raise ValueError(f"Unsupported DB scheme: {parsed.scheme}. Use postgres:// or mysql://")


def seed_rds(db_url):
    conn, dialect = _get_db_connection(db_url)
    cur = conn.cursor()
    token = str(uuid.uuid4())
    checksum = hashlib.sha256(token.encode()).hexdigest()

    print(f"\n🌱 Seeding RDS validation data ({dialect})...")

    if dialect == "postgres":
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {VALIDATION_TABLE} (
                id SERIAL PRIMARY KEY,
                migration_token VARCHAR(64) NOT NULL,
                checksum VARCHAR(128) NOT NULL,
                created_at TIMESTAMP DEFAULT NOW(),
                test_data JSONB
            )
        """)
    else:
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {VALIDATION_TABLE} (
                id INT AUTO_INCREMENT PRIMARY KEY,
                migration_token VARCHAR(64) NOT NULL,
                checksum VARCHAR(128) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                test_data JSON
            )
        """)

    test_data = json.dumps({
        "sample_rows": [
            {"name": "Alice", "value": 42},
            {"name": "Bob", "value": 99},
            {"name": "Charlie", "value": 7},
        ],
        "purpose": "migration_validation",
    })

    if dialect == "postgres":
        cur.execute(
            f"INSERT INTO {VALIDATION_TABLE} (migration_token, checksum, test_data) VALUES (%s, %s, %s)",
            (token, checksum, test_data),
        )
    else:
        cur.execute(
            f"INSERT INTO {VALIDATION_TABLE} (migration_token, checksum, test_data) VALUES (%s, %s, %s)",
            (token, checksum, test_data),
        )

    conn.commit()
    cur.close()
    conn.close()

    print(f"  ✅ Table: {VALIDATION_TABLE}")
    print(f"  ✅ Token: {token}")
    print(f"  ✅ Checksum: {checksum}")
    print(f"  ✅ Test data: 3 sample rows inserted")
    return {"token": token, "table": VALIDATION_TABLE}


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
        print(f"  ❌ Table or query failed: {e}")
        return False
    finally:
        cur.close()
        conn.close()

    if not row:
        print(f"  ❌ Token not found in {VALIDATION_TABLE}")
        return False

    found_token, found_checksum, found_data = row[0], row[1], row[2]

    if found_token == expected_token and found_checksum == expected_checksum:
        print(f"  ✅ Token matched: {found_token}")
        print(f"  ✅ Checksum matched: {found_checksum}")
        data = json.loads(found_data) if isinstance(found_data, str) else found_data
        print(f"  ✅ Test data rows: {len(data.get('sample_rows', []))}")
        return True
    else:
        print(f"  ❌ Checksum mismatch: expected={expected_checksum}, found={found_checksum}")
        return False


# ── CLI ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Seed test data before migration, verify after",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  # Seed EC2 marker file (via SSM)
  %(prog)s seed-ec2 -c config.yaml -i i-0abc123

  # Verify EC2 marker on target instance
  %(prog)s verify-ec2 -c config.yaml -i i-0xyz789 --token <TOKEN>

  # Seed RDS validation record
  %(prog)s seed-rds --db-url "postgres://user:pass@mydb.xxx.rds.amazonaws.com:5432/mydb"

  # Verify RDS validation on target
  %(prog)s verify-rds --db-url "postgres://user:pass@target.xxx.rds.amazonaws.com:5432/mydb" --token <TOKEN>
""")
    sub = parser.add_subparsers(dest="command", required=True)

    # seed-ec2
    p = sub.add_parser("seed-ec2", help="Create marker file on EC2 via SSM")
    p.add_argument("-c", "--config", default="config.yaml")
    p.add_argument("-i", "--instance-id", required=True)

    # verify-ec2
    p = sub.add_parser("verify-ec2", help="Verify marker file on target EC2")
    p.add_argument("-c", "--config", default="config.yaml")
    p.add_argument("-i", "--instance-id", required=True, help="Target instance ID")
    p.add_argument("--token", required=True, help="Token from seed-ec2 output")
    p.add_argument("--target", action="store_true", help="Use target account profile")

    # seed-rds
    p = sub.add_parser("seed-rds", help="Insert validation record into RDS")
    p.add_argument("--db-url", required=True, help="postgres://user:pass@host:port/db or mysql://...")

    # verify-rds
    p = sub.add_parser("verify-rds", help="Verify validation record on target RDS")
    p.add_argument("--db-url", required=True, help="Target DB URL")
    p.add_argument("--token", required=True, help="Token from seed-rds output")

    args = parser.parse_args()

    if args.command == "seed-ec2":
        cfg = load_config(args.config)
        session = boto3.Session(profile_name=cfg["source"]["profile"], region_name=cfg["source"]["region"])
        result = seed_ec2(session, args.instance_id)
        print(f"\n💾 Save this token for verification: {result['token']}")

    elif args.command == "verify-ec2":
        cfg = load_config(args.config)
        key = "target" if args.target else "source"
        session = boto3.Session(profile_name=cfg[key]["profile"], region_name=cfg[key]["region"])
        ok = verify_ec2(session, args.instance_id, args.token)
        sys.exit(0 if ok else 1)

    elif args.command == "seed-rds":
        result = seed_rds(args.db_url)
        print(f"\n💾 Save this token for verification: {result['token']}")

    elif args.command == "verify-rds":
        ok = verify_rds(args.db_url, args.token)
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
