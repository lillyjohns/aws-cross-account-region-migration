#!/usr/bin/env python3
"""Clean up all resources tagged MigrationPOC=true, then delete CloudFormation stacks."""

import argparse, subprocess, sys, time, yaml, boto3


def load_config(path):
    with open(path) as f:
        return yaml.safe_load(f)


TAG_KEY = "MigrationPOC"
TAG_VALUE = "true"


def clean_ec2(session, region, label):
    ec2 = session.client("ec2")

    # Terminate instances
    instances = ec2.describe_instances(Filters=[{"Name": f"tag:{TAG_KEY}", "Values": [TAG_VALUE]},
                                                 {"Name": "instance-state-name", "Values": ["running", "stopped"]}])
    ids = [i["InstanceId"] for r in instances["Reservations"] for i in r["Instances"]]
    if ids:
        print(f"  [{label}] Terminating {len(ids)} instances: {ids}")
        ec2.terminate_instances(InstanceIds=ids)
        waiter = ec2.get_waiter("instance_terminated")
        waiter.wait(InstanceIds=ids)
        print(f"  [{label}] ✅ Instances terminated")

    # Deregister AMIs
    amis = ec2.describe_images(Owners=["self"], Filters=[{"Name": f"tag:{TAG_KEY}", "Values": [TAG_VALUE]}])
    for ami in amis["Images"]:
        print(f"  [{label}] Deregistering AMI {ami['ImageId']}")
        # Get snapshot IDs before deregistering
        snap_ids = [bdm["Ebs"]["SnapshotId"] for bdm in ami.get("BlockDeviceMappings", []) if "Ebs" in bdm and "SnapshotId" in bdm["Ebs"]]
        ec2.deregister_image(ImageId=ami["ImageId"])
        for sid in snap_ids:
            try:
                ec2.delete_snapshot(SnapshotId=sid)
                print(f"  [{label}] Deleted snapshot {sid}")
            except Exception:
                pass

    # Delete any remaining tagged snapshots
    snaps = ec2.describe_snapshots(OwnerIds=["self"], Filters=[{"Name": f"tag:{TAG_KEY}", "Values": [TAG_VALUE]}])
    for s in snaps["Snapshots"]:
        try:
            ec2.delete_snapshot(SnapshotId=s["SnapshotId"])
            print(f"  [{label}] Deleted snapshot {s['SnapshotId']}")
        except Exception:
            pass


def clean_rds(session, region, label):
    rds = session.client("rds")

    # Delete tagged DB instances
    dbs = rds.describe_db_instances()
    for db in dbs["DBInstances"]:
        arn = db["DBInstanceArn"]
        tags = rds.list_tags_for_resource(ResourceName=arn)["TagList"]
        if any(t["Key"] == TAG_KEY and t["Value"] == TAG_VALUE for t in tags):
            print(f"  [{label}] Deleting DB instance {db['DBInstanceIdentifier']}")
            rds.delete_db_instance(DBInstanceIdentifier=db["DBInstanceIdentifier"], SkipFinalSnapshot=True)

    # Wait for DB deletions
    for db in dbs["DBInstances"]:
        arn = db["DBInstanceArn"]
        tags = rds.list_tags_for_resource(ResourceName=arn)["TagList"]
        if any(t["Key"] == TAG_KEY and t["Value"] == TAG_VALUE for t in tags):
            try:
                waiter = rds.get_waiter("db_instance_deleted")
                waiter.wait(DBInstanceIdentifier=db["DBInstanceIdentifier"])
                print(f"  [{label}] ✅ DB {db['DBInstanceIdentifier']} deleted")
            except Exception:
                pass

    # Delete tagged snapshots
    snaps = rds.describe_db_snapshots(SnapshotType="manual")
    for s in snaps["DBSnapshots"]:
        tags = rds.list_tags_for_resource(ResourceName=s["DBSnapshotArn"])["TagList"]
        if any(t["Key"] == TAG_KEY and t["Value"] == TAG_VALUE for t in tags):
            print(f"  [{label}] Deleting RDS snapshot {s['DBSnapshotIdentifier']}")
            try:
                rds.delete_db_snapshot(DBSnapshotIdentifier=s["DBSnapshotIdentifier"])
            except Exception:
                pass


def main():
    parser = argparse.ArgumentParser(description="Clean up all migration POC resources")
    parser.add_argument("-c", "--config", default="scripts/config.yaml")
    parser.add_argument("--skip-stacks", action="store_true", help="Skip CloudFormation stack deletion")
    args = parser.parse_args()

    cfg = load_config(args.config)
    src, tgt = cfg["source"], cfg["target"]

    src_session = boto3.Session(profile_name=src["profile"], region_name=src["region"])
    tgt_session = boto3.Session(profile_name=tgt["profile"], region_name=tgt["region"])

    print(f"\n🧹 Cleaning up MigrationPOC=true resources...\n")

    print("── EC2 ──")
    clean_ec2(tgt_session, tgt["region"], "target")
    clean_ec2(src_session, src["region"], "source")

    print("\n── RDS ──")
    clean_rds(tgt_session, tgt["region"], "target")
    clean_rds(src_session, src["region"], "source")

    print("\n✅ Migration artifacts cleaned up")

    if not args.skip_stacks:
        print("\n── CloudFormation Stacks ──")
        subprocess.run(["make", "destroy"])


if __name__ == "__main__":
    main()
