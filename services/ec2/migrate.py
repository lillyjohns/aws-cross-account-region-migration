#!/usr/bin/env python3
"""EC2 cross-account cross-region migration via AMI share + copy + launch."""

import argparse, sys, time, boto3
from services.shared.utils import load_config, wait_for


def migrate_instance(cfg, instance_id, dry_run=False):
    src, tgt = cfg["source"], cfg["target"]
    kms_key = cfg["target_kms_key_arn"]
    subnet_id = cfg["target_subnet_id"]
    sg_id = cfg["target_security_group_id"]
    instance_profile = cfg["target_instance_profile_arn"]

    src_ec2 = boto3.Session(profile_name=src["profile"], region_name=src["region"]).client("ec2")
    tgt_ec2 = boto3.Session(profile_name=tgt["profile"], region_name=tgt["region"]).client("ec2")

    ts = time.strftime("%Y%m%d-%H%M%S")
    ami_name = f"migration-{instance_id}-{ts}"

    print(f"\n[1/5] Creating AMI from {instance_id}...")
    if dry_run:
        print(f"  DRY RUN: would create AMI '{ami_name}', copy to {tgt['region']}, and launch")
        return
    # Flush disk before snapshot (NoReboot mode)
    ssm = boto3.Session(profile_name=src["profile"], region_name=src["region"]).client("ssm")
    try:
        ssm.send_command(InstanceIds=[instance_id], DocumentName="AWS-RunShellScript",
                         Parameters={"commands": ["sync"]}, Comment="pre-snapshot-sync")
        time.sleep(5)
    except Exception:
        pass  # Best effort — continue even if SSM fails
    resp = src_ec2.create_image(InstanceId=instance_id, Name=ami_name, NoReboot=True)
    ami_id = resp["ImageId"]
    print(f"  ✅ AMI: {ami_id}")
    wait_for(lambda: src_ec2.describe_images(ImageIds=[ami_id]),
             lambda r: r["Images"][0]["State"] == "available", f"AMI {ami_id} available")

    print(f"\n[2/5] Sharing AMI with account {tgt['account_id']}...")
    src_ec2.modify_image_attribute(ImageId=ami_id, LaunchPermission={"Add": [{"UserId": tgt["account_id"]}]})
    for bdm in src_ec2.describe_images(ImageIds=[ami_id])["Images"][0].get("BlockDeviceMappings", []):
        snap_id = bdm.get("Ebs", {}).get("SnapshotId")
        if snap_id:
            src_ec2.modify_snapshot_attribute(SnapshotId=snap_id, Attribute="createVolumePermission",
                                              OperationType="add", UserIds=[tgt["account_id"]])
            print(f"  ✅ Shared snapshot {snap_id}")

    print(f"\n[3/5] Copying AMI to {tgt['region']}...")
    copy_resp = tgt_ec2.copy_image(Name=ami_name, SourceImageId=ami_id, SourceRegion=src["region"],
                                    Encrypted=True, KmsKeyId=kms_key)
    target_ami = copy_resp["ImageId"]
    print(f"  ✅ Target AMI: {target_ami}")
    wait_for(lambda: tgt_ec2.describe_images(ImageIds=[target_ami]),
             lambda r: r["Images"][0]["State"] == "available", f"Target AMI {target_ami} available", interval=30)

    src_inst = src_ec2.describe_instances(InstanceIds=[instance_id])["Reservations"][0]["Instances"][0]
    inst_type = src_inst["InstanceType"]

    print(f"\n[4/5] Launching instance in {tgt['region']}...")
    run_resp = tgt_ec2.run_instances(
        ImageId=target_ami, InstanceType=inst_type, MinCount=1, MaxCount=1,
        SubnetId=subnet_id, SecurityGroupIds=[sg_id],
        IamInstanceProfile={"Arn": instance_profile},
        TagSpecifications=[{"ResourceType": "instance",
                            "Tags": [{"Key": "Name", "Value": f"migrated-{instance_id}"}]}],
    )
    target_instance_id = run_resp["Instances"][0]["InstanceId"]
    print(f"  ✅ Instance: {target_instance_id}")
    wait_for(lambda: tgt_ec2.describe_instances(InstanceIds=[target_instance_id]),
             lambda r: r["Reservations"][0]["Instances"][0]["State"]["Name"] == "running",
             f"Instance {target_instance_id} running")

    print(f"\n[5/5] ✅ Migration complete!")
    print(f"  Target instance: {target_instance_id} in {tgt['region']}")
    print(f"  Target AMI: {target_ami}")
    return target_instance_id


def main():
    parser = argparse.ArgumentParser(description="Migrate EC2 instances cross-account cross-region")
    parser.add_argument("-c", "--config", default="scripts/config.yaml")
    parser.add_argument("-i", "--instance-id", help="Single instance ID to migrate")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    cfg = load_config(args.config)
    instances = [args.instance_id] if args.instance_id else cfg["ec2"]["instance_ids"]

    print(f"EC2 Migration: {cfg['source']['region']} → {cfg['target']['region']}")
    for iid in instances:
        try:
            migrate_instance(cfg, iid, dry_run=args.dry_run)
        except Exception as e:
            print(f"\n❌ Failed to migrate {iid}: {e}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
