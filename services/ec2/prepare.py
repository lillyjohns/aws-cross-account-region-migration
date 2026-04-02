#!/usr/bin/env python3
"""EC2 prepare: seed marker file via SSM for data integrity validation."""

import argparse, hashlib, json, sys, time, uuid, boto3
from services.shared.utils import load_config

MARKER_PATH = "/tmp/migration-marker.json"


def _ssm_run(ssm, instance_id, commands, label="command"):
    resp = ssm.send_command(
        InstanceIds=[instance_id],
        DocumentName="AWS-RunShellScript",
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
    return {"instance_id": instance_id, "token": token}


def fingerprint(session, instance_id):
    ec2 = session.client("ec2")
    inst = ec2.describe_instances(InstanceIds=[instance_id])["Reservations"][0]["Instances"][0]
    volumes = []
    for bdm in inst.get("BlockDeviceMappings", []):
        vol_id = bdm.get("Ebs", {}).get("VolumeId")
        if vol_id:
            vol = ec2.describe_volumes(VolumeIds=[vol_id])["Volumes"][0]
            volumes.append({"device": bdm["DeviceName"], "size_gb": vol["Size"], "volume_type": vol["VolumeType"], "encrypted": vol["Encrypted"]})

    fp = {
        "instance_id": instance_id, "instance_type": inst["InstanceType"],
        "architecture": inst["Architecture"], "platform": inst.get("PlatformDetails", "Linux/UNIX"),
        "volumes": sorted(volumes, key=lambda v: v["device"]),
        "total_storage_gb": sum(v["size_gb"] for v in volumes), "ami_id": inst["ImageId"],
    }
    print(f"\n🔍 EC2: {instance_id}")
    print(f"  Type: {fp['instance_type']} | Storage: {fp['total_storage_gb']} GB | Volumes: {len(fp['volumes'])}")
    return fp


def main():
    parser = argparse.ArgumentParser(description="EC2 prepare: seed marker + fingerprint")
    parser.add_argument("-c", "--config", default="scripts/config.yaml")
    parser.add_argument("-i", "--instance-id", help="Single instance ID")
    args = parser.parse_args()

    cfg = load_config(args.config)
    session = boto3.Session(profile_name=cfg["source"]["profile"], region_name=cfg["source"]["region"])
    instances = [args.instance_id] if args.instance_id else cfg["ec2"]["instance_ids"]

    for iid in instances:
        result = seed_ec2(session, iid)
        fingerprint(session, iid)
        print(f"\n💾 Save this token for verification: {result['token']}")


if __name__ == "__main__":
    main()
