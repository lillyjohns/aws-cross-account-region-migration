#!/usr/bin/env python3
"""RDS verify: check validation row on target DB via SSM."""

import argparse, hashlib, sys, time, boto3
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


def verify_rds(session, instance_id, rds_endpoint, db_name, db_user, db_password, expected_token):
    ssm = session.client("ssm")
    expected_checksum = hashlib.sha256(expected_token.encode()).hexdigest()

    sql = f"SELECT migration_token, checksum FROM {VALIDATION_TABLE} WHERE migration_token = '{expected_token}';"

    print(f"\n🔍 Verifying RDS via SSM on {instance_id}...")
    result = _ssm_run(ssm, instance_id, [
        f'PGPASSWORD="{db_password}" psql -h {rds_endpoint} -U {db_user} -d {db_name} -t -A -c "{sql}"'
    ], label="rds-verify")

    if result["Status"] != "Success":
        print(f"  ❌ Failed: {result.get('StandardErrorContent', 'unknown')}")
        return False

    output = result.get("StandardOutputContent", "").strip()
    if not output:
        print(f"  ❌ Token not found in {VALIDATION_TABLE}")
        return False

    parts = output.split("|")
    if len(parts) >= 2 and parts[0] == expected_token and parts[1] == expected_checksum:
        print(f"  ✅ Token matched: {parts[0]}")
        print(f"  ✅ Checksum matched")
        return True
    else:
        print(f"  ❌ Mismatch: {output}")
        return False


def main():
    parser = argparse.ArgumentParser(description="RDS verify: check validation row via SSM")
    parser.add_argument("-c", "--config", default="scripts/config.yaml")
    parser.add_argument("--db-password", required=True, help="RDS master password")
    parser.add_argument("--token", required=True, help="Token from prepare step")
    parser.add_argument("--target-instance-id", help="EC2 instance in target VPC (for post-migration verify)")
    parser.add_argument("--target-rds-endpoint", help="Target RDS endpoint (for post-migration verify)")
    args = parser.parse_args()

    cfg = load_config(args.config)

    if args.target_instance_id and args.target_rds_endpoint:
        # Verify on target account
        session = boto3.Session(profile_name=cfg["target"]["profile"], region_name=cfg["target"]["region"])
        rds_endpoint = args.target_rds_endpoint
        instance_id = args.target_instance_id
        db_user = "dbadmin"
        db_name = "migrationtest"
    else:
        # Verify on source account
        session = boto3.Session(profile_name=cfg["source"]["profile"], region_name=cfg["source"]["region"])
        instance_id = cfg["ec2"]["instance_ids"][0]
        rds = session.client("rds")
        db = rds.describe_db_instances(DBInstanceIdentifier=cfg["rds"]["instances"][0]["db_instance_id"])["DBInstances"][0]
        rds_endpoint = db["Endpoint"]["Address"]
        db_name = db.get("DBName", "migrationtest")
        db_user = db["MasterUsername"]

    ok = verify_rds(session, instance_id, rds_endpoint, db_name, db_user, args.db_password, args.token)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
