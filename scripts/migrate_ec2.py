#!/usr/bin/env python3
"""EC2 cross-account cross-region migration via AMI share + copy."""

import argparse, sys, time, yaml, boto3


def load_config(path):
    with open(path) as f:
        return yaml.safe_load(f)


def wait_for(describe_fn, check_fn, label, interval=15, timeout=1800):
    """Poll until check_fn(response) returns True."""
    elapsed = 0
    while elapsed < timeout:
        resp = describe_fn()
        if check_fn(resp):
            return resp
        print(f"  ⏳ {label}... ({elapsed}s)")
        time.sleep(interval)
        elapsed += interval
    raise TimeoutError(f"Timed out waiting for {label}")


def migrate_instance(cfg, instance_id, dry_run=False):
    src = cfg["source"]
    tgt = cfg["target"]
    kms_key = cfg["target_kms_key_arn"]

    src_ec2 = boto3.Session(profile_name=src["profile"], region_name=src["region"]).client("ec2")
    tgt_ec2 = boto3.Session(profile_name=tgt["profile"], region_name=tgt["region"]).client("ec2")

    ts = time.strftime("%Y%m%d-%H%M%S")
    ami_name = f"migration-{instance_id}-{ts}"

    # Step 1: Create AMI
    print(f"\n[1/5] Creating AMI from {instance_id}...")
    if dry_run:
        print(f"  DRY RUN: would create AMI '{ami_name}'")
        return
    resp = src_ec2.create_image(InstanceId=instance_id, Name=ami_name, NoReboot=True)
    ami_id = resp["ImageId"]
    print(f"  ✅ AMI: {ami_id}")

    wait_for(
        lambda: src_ec2.describe_images(ImageIds=[ami_id]),
        lambda r: r["Images"][0]["State"] == "available",
        f"AMI {ami_id} available",
    )

    # Step 2: Share AMI with target account
    print(f"\n[2/5] Sharing AMI with account {tgt['account_id']}...")
    src_ec2.modify_image_attribute(
        ImageId=ami_id,
        LaunchPermission={"Add": [{"UserId": tgt["account_id"]}]},
    )
    # Share underlying snapshots too
    for bdm in src_ec2.describe_images(ImageIds=[ami_id])["Images"][0].get("BlockDeviceMappings", []):
        snap_id = bdm.get("Ebs", {}).get("SnapshotId")
        if snap_id:
            src_ec2.modify_snapshot_attribute(
                SnapshotId=snap_id,
                Attribute="createVolumePermission",
                OperationType="add",
                UserIds=[tgt["account_id"]],
            )
            print(f"  ✅ Shared snapshot {snap_id}")
    print(f"  ✅ AMI shared")

    # Step 3: Copy AMI to target region
    print(f"\n[3/5] Copying AMI to {tgt['region']}...")
    copy_resp = tgt_ec2.copy_image(
        Name=ami_name,
        SourceImageId=ami_id,
        SourceRegion=src["region"],
        Encrypted=True,
        KmsKeyId=kms_key,
    )
    target_ami = copy_resp["ImageId"]
    print(f"  ✅ Target AMI: {target_ami}")

    wait_for(
        lambda: tgt_ec2.describe_images(ImageIds=[target_ami]),
        lambda r: r["Images"][0]["State"] == "available",
        f"Target AMI {target_ami} available",
        interval=30,
    )

    # Step 4: Get source instance config
    print(f"\n[4/5] Reading source instance config...")
    src_inst = src_ec2.describe_instances(InstanceIds=[instance_id])["Reservations"][0]["Instances"][0]
    inst_type = src_inst["InstanceType"]
    print(f"  Instance type: {inst_type}")

    # Step 5: Summary
    print(f"\n[5/5] ✅ Migration complete!")
    print(f"  Target AMI: {target_ami} in {tgt['region']}")
    print(f"  Launch with:")
    print(f"    aws ec2 run-instances --image-id {target_ami} --instance-type {inst_type} \\")
    print(f"      --subnet-id <TARGET_SUBNET> --security-group-ids <TARGET_SG> \\")
    print(f"      --region {tgt['region']} --profile {tgt['profile']}")

    return target_ami


def main():
    parser = argparse.ArgumentParser(description="Migrate EC2 instances cross-account cross-region")
    parser.add_argument("-c", "--config", default="config.yaml", help="Config file path")
    parser.add_argument("-i", "--instance-id", help="Override: single instance ID to migrate")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done")
    args = parser.parse_args()

    cfg = load_config(args.config)
    instances = [args.instance_id] if args.instance_id else cfg["ec2"]["instance_ids"]

    print(f"EC2 Migration: {cfg['source']['region']} → {cfg['target']['region']}")
    print(f"Source account: {cfg['source']['account_id']}")
    print(f"Target account: {cfg['target']['account_id']}")
    print(f"Instances: {instances}")

    for iid in instances:
        try:
            migrate_instance(cfg, iid, dry_run=args.dry_run)
        except Exception as e:
            print(f"\n❌ Failed to migrate {iid}: {e}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
