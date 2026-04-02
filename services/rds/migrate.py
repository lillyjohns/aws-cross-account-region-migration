#!/usr/bin/env python3
"""RDS cross-account cross-region migration via snapshot share + copy + restore."""

import argparse, sys, time, boto3
from services.shared.utils import load_config, wait_for


def migrate_rds(cfg, db_id, target_instance_class, target_subnet_group, dry_run=False):
    src, tgt = cfg["source"], cfg["target"]
    kms_key = cfg["target_kms_key_arn"]
    src_rds = boto3.Session(profile_name=src["profile"], region_name=src["region"]).client("rds")
    tgt_rds = boto3.Session(profile_name=tgt["profile"], region_name=tgt["region"]).client("rds")

    ts = time.strftime("%Y%m%d-%H%M%S")
    snap_id = f"migration-{db_id}-{ts}"

    print(f"\n[1/5] Creating snapshot of {db_id}...")
    if dry_run:
        print(f"  DRY RUN: would create snapshot '{snap_id}'")
        return
    src_rds.create_db_snapshot(DBInstanceIdentifier=db_id, DBSnapshotIdentifier=snap_id)
    wait_for(lambda: src_rds.describe_db_snapshots(DBSnapshotIdentifier=snap_id),
             lambda r: r["DBSnapshots"][0]["Status"] == "available", f"Snapshot {snap_id} available", interval=30)
    snap_arn = src_rds.describe_db_snapshots(DBSnapshotIdentifier=snap_id)["DBSnapshots"][0]["DBSnapshotArn"]
    print(f"  ✅ Snapshot: {snap_arn}")

    print(f"\n[2/5] Sharing snapshot with account {tgt['account_id']}...")
    src_rds.modify_db_snapshot_attribute(DBSnapshotIdentifier=snap_id, AttributeName="restore",
                                         ValuesToAdd=[tgt["account_id"]])
    print(f"  ✅ Shared")

    target_snap_id = f"copied-{snap_id}"
    print(f"\n[3/5] Copying snapshot to {tgt['region']} (re-encrypting)...")
    tgt_rds.copy_db_snapshot(SourceDBSnapshotIdentifier=snap_arn, TargetDBSnapshotIdentifier=target_snap_id,
                              KmsKeyId=kms_key, SourceRegion=src["region"], CopyTags=True)
    wait_for(lambda: tgt_rds.describe_db_snapshots(DBSnapshotIdentifier=target_snap_id),
             lambda r: r["DBSnapshots"][0]["Status"] == "available",
             f"Target snapshot {target_snap_id} available", interval=60)
    print(f"  ✅ Copied: {target_snap_id}")

    target_db_id = f"{db_id}-migrated"
    print(f"\n[4/5] Restoring DB instance {target_db_id}...")
    restore_params = {"DBInstanceIdentifier": target_db_id, "DBSnapshotIdentifier": target_snap_id,
                      "DBInstanceClass": target_instance_class, "MultiAZ": False, "PubliclyAccessible": False}
    if target_subnet_group:
        restore_params["DBSubnetGroupName"] = target_subnet_group
    if cfg.get("target_security_group_id"):
        restore_params["VpcSecurityGroupIds"] = [cfg["target_security_group_id"]]
    tgt_rds.restore_db_instance_from_db_snapshot(**restore_params)
    wait_for(lambda: tgt_rds.describe_db_instances(DBInstanceIdentifier=target_db_id),
             lambda r: r["DBInstances"][0]["DBInstanceStatus"] == "available",
             f"DB {target_db_id} available", interval=60)
    endpoint = tgt_rds.describe_db_instances(DBInstanceIdentifier=target_db_id)["DBInstances"][0]["Endpoint"]

    print(f"\n[5/5] ✅ Migration complete!")
    print(f"  Endpoint: {endpoint['Address']}:{endpoint['Port']}")
    print(f"  Region:   {tgt['region']}")
    return target_db_id


def main():
    parser = argparse.ArgumentParser(description="Migrate RDS instances cross-account cross-region")
    parser.add_argument("-c", "--config", default="scripts/config.yaml")
    parser.add_argument("-d", "--db-instance-id", help="Single DB instance ID")
    parser.add_argument("--instance-class", default="db.t3.micro")
    parser.add_argument("--subnet-group", help="Target DB subnet group")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    cfg = load_config(args.config)
    print(f"RDS Migration: {cfg['source']['region']} → {cfg['target']['region']}")

    if args.db_instance_id:
        instances = [{"db_instance_id": args.db_instance_id, "target_instance_class": args.instance_class,
                      "target_subnet_group": args.subnet_group}]
    else:
        instances = cfg["rds"]["instances"]

    for db in instances:
        try:
            migrate_rds(cfg, db["db_instance_id"], db.get("target_instance_class", args.instance_class),
                        db.get("target_subnet_group", args.subnet_group), dry_run=args.dry_run)
        except Exception as e:
            print(f"\n❌ Failed to migrate {db['db_instance_id']}: {e}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
