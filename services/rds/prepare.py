#!/usr/bin/env python3
"""RDS prepare: seed validation row via SSM (runs psql on EC2 in same VPC)."""

import argparse, hashlib, json, sys, time, uuid, boto3
from services.shared.utils import load_config

VALIDATION_TABLE = "_migration_validation"


def _ssm_run(ssm, instance_id, commands, label="command"):
    resp = ssm.send_command(
        InstanceIds=[instance_id], DocumentName="AWS-RunShellScript",
        Parameters={"commands": commands}, Comment=f"migration-{label}",
    )
    cmd_id = resp["Command"]["CommandId"]
    for _ in range(30):
        time.sleep(2)
        result = ssm.get_command_invocation(CommandId=cmd_id, InstanceId=instance_id)
        if result["Status"] in ("Success", "Failed", "TimedOut", "Cancelled"):
            return result
    raise TimeoutError(f"SSM command timed out on {instance_id}")


def seed_rds(session, instance_id, rds_endpoint, db_name, db_user, db_password):
    ssm = session.client("ssm")
    token = str(uuid.uuid4())
    checksum = hashlib.sha256(token.encode()).hexdigest()
    test_data = json.dumps({"sample_rows": [{"name": "Alice", "value": 42}, {"name": "Bob", "value": 99}]})

    sql = f"""
CREATE TABLE IF NOT EXISTS {VALIDATION_TABLE} (
    id SERIAL PRIMARY KEY, migration_token VARCHAR(64) NOT NULL,
    checksum VARCHAR(128) NOT NULL, created_at TIMESTAMP DEFAULT NOW(), test_data JSONB
);
INSERT INTO {VALIDATION_TABLE} (migration_token, checksum, test_data)
VALUES ('{token}', '{checksum}', '{test_data}');
SELECT migration_token, checksum FROM {VALIDATION_TABLE} WHERE migration_token = '{token}';
"""

    print(f"\n🌱 Seeding RDS via SSM on {instance_id}...")
    result = _ssm_run(ssm, instance_id, [
        f'PGPASSWORD="{db_password}" psql -h {rds_endpoint} -U {db_user} -d {db_name} -c "{sql}"'
    ], label="rds-seed")

    if result["Status"] != "Success":
        print(f"  ❌ Failed: {result.get('StandardErrorContent', 'unknown')}")
        sys.exit(1)

    print(f"  ✅ Token: {token}")
    print(f"  ✅ Checksum: {checksum}")
    print(result.get("StandardOutputContent", ""))
    return {"token": token}


def main():
    parser = argparse.ArgumentParser(description="RDS prepare: seed validation row via SSM")
    parser.add_argument("-c", "--config", default="scripts/config.yaml")
    parser.add_argument("--db-password", required=True, help="RDS master password")
    args = parser.parse_args()

    cfg = load_config(args.config)
    session = boto3.Session(profile_name=cfg["source"]["profile"], region_name=cfg["source"]["region"])

    # Get EC2 instance ID and RDS endpoint from config
    instance_id = cfg["ec2"]["instance_ids"][0]
    rds_cfg = cfg["rds"]["instances"][0]

    # Get RDS endpoint
    rds = session.client("rds")
    db = rds.describe_db_instances(DBInstanceIdentifier=rds_cfg["db_instance_id"])["DBInstances"][0]
    rds_endpoint = db["Endpoint"]["Address"]
    db_name = db.get("DBName", "migrationtest")
    db_user = db["MasterUsername"]

    result = seed_rds(session, instance_id, rds_endpoint, db_name, db_user, args.db_password)
    print(f"\n💾 Save this token for verification: {result['token']}")


if __name__ == "__main__":
    main()
