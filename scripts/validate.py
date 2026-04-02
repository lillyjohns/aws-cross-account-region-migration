#!/usr/bin/env python3
"""Pre/post migration validation — capture fingerprints and compare."""

import argparse, hashlib, json, sys, time, yaml, boto3
from pathlib import Path


def load_config(path):
    with open(path) as f:
        return yaml.safe_load(f)


# ── EC2 ──────────────────────────────────────────────────────────────

def fingerprint_ec2(session, instance_id):
    ec2 = session.client("ec2")
    inst = ec2.describe_instances(InstanceIds=[instance_id])["Reservations"][0]["Instances"][0]

    volumes = []
    for bdm in inst.get("BlockDeviceMappings", []):
        vol_id = bdm.get("Ebs", {}).get("VolumeId")
        if vol_id:
            vol = ec2.describe_volumes(VolumeIds=[vol_id])["Volumes"][0]
            volumes.append({
                "device": bdm["DeviceName"],
                "volume_id": vol_id,
                "size_gb": vol["Size"],
                "volume_type": vol["VolumeType"],
                "encrypted": vol["Encrypted"],
            })

    return {
        "instance_id": instance_id,
        "instance_type": inst["InstanceType"],
        "architecture": inst["Architecture"],
        "platform": inst.get("PlatformDetails", "Linux/UNIX"),
        "root_device": inst["RootDeviceName"],
        "volumes": sorted(volumes, key=lambda v: v["device"]),
        "total_storage_gb": sum(v["size_gb"] for v in volumes),
        "ami_id": inst["ImageId"],
        "state": inst["State"]["Name"],
    }


def fingerprint_ami(session, ami_id):
    ec2 = session.client("ec2")
    img = ec2.describe_images(ImageIds=[ami_id])["Images"][0]

    snaps = []
    for bdm in img.get("BlockDeviceMappings", []):
        ebs = bdm.get("Ebs", {})
        if ebs.get("SnapshotId"):
            snaps.append({
                "device": bdm["DeviceName"],
                "snapshot_id": ebs["SnapshotId"],
                "size_gb": ebs.get("VolumeSize"),
                "encrypted": ebs.get("Encrypted", False),
            })

    return {
        "ami_id": ami_id,
        "name": img.get("Name"),
        "architecture": img["Architecture"],
        "platform": img.get("PlatformDetails", "Linux/UNIX"),
        "state": img["State"],
        "snapshots": sorted(snaps, key=lambda s: s["device"]),
        "total_storage_gb": sum(s["size_gb"] for s in snaps if s["size_gb"]),
    }


# ── S3 ──────────────────────────────────────────────────────────────

def fingerprint_s3(session, bucket, prefix="", sample_size=100):
    s3 = session.client("s3")
    paginator = s3.get_paginator("list_objects_v2")

    total_objects, total_size = 0, 0
    etag_digest = hashlib.md5()
    sample_objects = []

    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            total_objects += 1
            total_size += obj["Size"]
            etag_digest.update(obj["ETag"].encode())
            if len(sample_objects) < sample_size:
                sample_objects.append({
                    "key": obj["Key"],
                    "size": obj["Size"],
                    "etag": obj["ETag"],
                })

    return {
        "bucket": bucket,
        "prefix": prefix or "(root)",
        "total_objects": total_objects,
        "total_size_bytes": total_size,
        "total_size_human": _human_size(total_size),
        "etag_checksum": etag_digest.hexdigest(),
        "sample_objects": sample_objects,
    }


# ── RDS ─────────────────────────────────────────────────────────────

def fingerprint_rds(session, db_instance_id):
    rds = session.client("rds")
    db = rds.describe_db_instances(DBInstanceIdentifier=db_instance_id)["DBInstances"][0]

    return {
        "db_instance_id": db_instance_id,
        "engine": db["Engine"],
        "engine_version": db["EngineVersion"],
        "instance_class": db["DBInstanceClass"],
        "storage_gb": db["AllocatedStorage"],
        "storage_type": db["StorageType"],
        "multi_az": db["MultiAZ"],
        "encrypted": db["StorageEncrypted"],
        "status": db["DBInstanceStatus"],
        "endpoint": db.get("Endpoint", {}).get("Address", "N/A"),
        "port": db.get("Endpoint", {}).get("Port", "N/A"),
        "parameter_group": db["DBParameterGroups"][0]["DBParameterGroupName"] if db.get("DBParameterGroups") else "N/A",
        "db_name": db.get("DBName", "N/A"),
    }


def fingerprint_rds_snapshot(session, snapshot_id):
    rds = session.client("rds")
    snap = rds.describe_db_snapshots(DBSnapshotIdentifier=snapshot_id)["DBSnapshots"][0]

    return {
        "snapshot_id": snapshot_id,
        "engine": snap["Engine"],
        "engine_version": snap["EngineVersion"],
        "storage_gb": snap["AllocatedStorage"],
        "encrypted": snap["Encrypted"],
        "status": snap["Status"],
        "source_db": snap.get("DBInstanceIdentifier", "N/A"),
    }


# ── Compare ─────────────────────────────────────────────────────────

def compare_fingerprints(pre, post, label):
    """Compare two fingerprint dicts, return list of diffs."""
    diffs = []
    # Keys to skip in comparison (expected to differ)
    skip = {"instance_id", "ami_id", "volume_id", "snapshot_id", "db_instance_id",
            "endpoint", "state", "status", "bucket", "sample_objects", "source_db",
            "parameter_group", "etag_checksum"}

    for key in set(list(pre.keys()) + list(post.keys())):
        if key in skip:
            continue
        pre_val = pre.get(key)
        post_val = post.get(key)
        if key == "volumes" or key == "snapshots":
            # Compare storage totals instead of individual IDs
            continue
        if pre_val != post_val:
            diffs.append({"field": key, "pre": pre_val, "post": post_val})

    return diffs


def print_comparison(label, pre, post, diffs):
    print(f"\n{'═' * 60}")
    print(f"  {label}")
    print(f"{'═' * 60}")

    if not diffs:
        print("  ✅ All comparable fields match")
    else:
        for d in diffs:
            match = "✅" if d["pre"] == d["post"] else "❌"
            print(f"  {match} {d['field']}: {d['pre']} → {d['post']}")

    # Always show key metrics side by side
    print(f"\n  Key Metrics:")
    for key in ["total_storage_gb", "total_objects", "total_size_bytes",
                "storage_gb", "engine", "engine_version", "architecture",
                "instance_type", "instance_class"]:
        if key in pre:
            match = "✅" if pre[key] == post.get(key) else "❌"
            print(f"    {match} {key}: {pre[key]} → {post.get(key, 'N/A')}")

    # S3: compare sample object ETags
    if "sample_objects" in pre and "sample_objects" in post:
        pre_map = {o["key"]: o["etag"] for o in pre["sample_objects"]}
        post_map = {o["key"]: o["etag"] for o in post["sample_objects"]}
        matched = sum(1 for k in pre_map if pre_map[k] == post_map.get(k))
        total = len(pre_map)
        icon = "✅" if matched == total else "⚠️"
        print(f"    {icon} Sample ETags matched: {matched}/{total}")


# ── Helpers ─────────────────────────────────────────────────────────

def _human_size(nbytes):
    for unit in ["B", "KB", "MB", "GB", "TB", "PB"]:
        if nbytes < 1024:
            return f"{nbytes:.2f} {unit}"
        nbytes /= 1024
    return f"{nbytes:.2f} EB"


def save_report(data, path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)
    print(f"\n📄 Report saved: {path}")


# ── Commands ────────────────────────────────────────────────────────

def cmd_pre(cfg, output_dir):
    src = cfg["source"]
    session = boto3.Session(profile_name=src["profile"], region_name=src["region"])
    report = {"phase": "pre", "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"), "source": src, "resources": {}}

    # EC2
    for iid in cfg.get("ec2", {}).get("instance_ids", []):
        print(f"\n🔍 EC2: {iid}")
        fp = fingerprint_ec2(session, iid)
        report["resources"][f"ec2:{iid}"] = fp
        print(f"  Type: {fp['instance_type']} | Storage: {fp['total_storage_gb']} GB | Volumes: {len(fp['volumes'])}")

    # S3
    for b in cfg.get("s3", {}).get("buckets", []):
        print(f"\n🔍 S3: {b['source']}")
        fp = fingerprint_s3(session, b["source"])
        report["resources"][f"s3:{b['source']}"] = fp
        print(f"  Objects: {fp['total_objects']} | Size: {fp['total_size_human']}")

    # RDS
    for db in cfg.get("rds", {}).get("instances", []):
        print(f"\n🔍 RDS: {db['db_instance_id']}")
        fp = fingerprint_rds(session, db["db_instance_id"])
        report["resources"][f"rds:{db['db_instance_id']}"] = fp
        print(f"  Engine: {fp['engine']} {fp['engine_version']} | Storage: {fp['storage_gb']} GB")

    save_report(report, f"{output_dir}/pre-migration.json")
    return report


def cmd_post(cfg, output_dir, post_resources):
    tgt = cfg["target"]
    session = boto3.Session(profile_name=tgt["profile"], region_name=tgt["region"])
    report = {"phase": "post", "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"), "target": tgt, "resources": {}}

    for res in post_resources:
        rtype, rid = res.split(":", 1)

        if rtype == "ec2-ami":
            print(f"\n🔍 EC2 AMI: {rid}")
            fp = fingerprint_ami(session, rid)
            report["resources"][f"ec2-ami:{rid}"] = fp
            print(f"  Arch: {fp['architecture']} | Storage: {fp['total_storage_gb']} GB | Snapshots: {len(fp['snapshots'])}")

        elif rtype == "ec2":
            print(f"\n🔍 EC2: {rid}")
            fp = fingerprint_ec2(session, rid)
            report["resources"][f"ec2:{rid}"] = fp
            print(f"  Type: {fp['instance_type']} | Storage: {fp['total_storage_gb']} GB")

        elif rtype == "s3":
            print(f"\n🔍 S3: {rid}")
            fp = fingerprint_s3(session, rid)
            report["resources"][f"s3:{rid}"] = fp
            print(f"  Objects: {fp['total_objects']} | Size: {fp['total_size_human']}")

        elif rtype == "rds":
            print(f"\n🔍 RDS: {rid}")
            fp = fingerprint_rds(session, rid)
            report["resources"][f"rds:{rid}"] = fp
            print(f"  Engine: {fp['engine']} {fp['engine_version']} | Storage: {fp['storage_gb']} GB")

        elif rtype == "rds-snap":
            print(f"\n🔍 RDS Snapshot: {rid}")
            fp = fingerprint_rds_snapshot(session, rid)
            report["resources"][f"rds-snap:{rid}"] = fp
            print(f"  Engine: {fp['engine']} {fp['engine_version']} | Storage: {fp['storage_gb']} GB")

    save_report(report, f"{output_dir}/post-migration.json")
    return report


def cmd_compare(output_dir, mappings):
    pre_path = f"{output_dir}/pre-migration.json"
    post_path = f"{output_dir}/post-migration.json"

    with open(pre_path) as f:
        pre = json.load(f)
    with open(post_path) as f:
        post = json.load(f)

    print(f"\n{'═' * 60}")
    print(f"  MIGRATION VALIDATION REPORT")
    print(f"  Pre:  {pre['timestamp']}")
    print(f"  Post: {post['timestamp']}")
    print(f"{'═' * 60}")

    all_pass = True
    for mapping in mappings:
        pre_key, post_key = mapping.split("=")
        pre_fp = pre["resources"].get(pre_key)
        post_fp = post["resources"].get(post_key)

        if not pre_fp:
            print(f"\n❌ Pre-migration key not found: {pre_key}")
            all_pass = False
            continue
        if not post_fp:
            print(f"\n❌ Post-migration key not found: {post_key}")
            all_pass = False
            continue

        diffs = compare_fingerprints(pre_fp, post_fp, f"{pre_key} → {post_key}")
        print_comparison(f"{pre_key} → {post_key}", pre_fp, post_fp, diffs)
        if diffs:
            all_pass = False

    print(f"\n{'═' * 60}")
    if all_pass:
        print("  ✅ VALIDATION PASSED — all resources match")
    else:
        print("  ⚠️  VALIDATION HAS DIFFERENCES — review above")
    print(f"{'═' * 60}\n")


def main():
    parser = argparse.ArgumentParser(
        description="Pre/post migration validation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  # Pre-check: capture source fingerprints
  %(prog)s pre -c config.yaml

  # Post-check: capture target fingerprints
  %(prog)s post -c config.yaml -r ec2-ami:ami-0abc123 s3:my-target-bucket rds:mydb-migrated

  # Compare pre vs post
  %(prog)s compare -m "ec2:i-0abc123=ec2-ami:ami-0abc123" "s3:my-source-bucket=s3:my-target-bucket" "rds:mydb-prod=rds:mydb-migrated"
""")
    sub = parser.add_subparsers(dest="command", required=True)

    # pre
    p_pre = sub.add_parser("pre", help="Capture source resource fingerprints")
    p_pre.add_argument("-c", "--config", default="config.yaml")
    p_pre.add_argument("-o", "--output-dir", default="./validation")

    # post
    p_post = sub.add_parser("post", help="Capture target resource fingerprints")
    p_post.add_argument("-c", "--config", default="config.yaml")
    p_post.add_argument("-o", "--output-dir", default="./validation")
    p_post.add_argument("-r", "--resources", nargs="+", required=True,
                        help="Target resources: ec2:ID, ec2-ami:ID, s3:BUCKET, rds:ID, rds-snap:ID")

    # compare
    p_cmp = sub.add_parser("compare", help="Compare pre vs post fingerprints")
    p_cmp.add_argument("-o", "--output-dir", default="./validation")
    p_cmp.add_argument("-m", "--mappings", nargs="+", required=True,
                       help="Pre=Post mappings: 'ec2:i-src=ec2-ami:ami-tgt' 's3:src-bucket=s3:tgt-bucket'")

    args = parser.parse_args()

    if args.command == "pre":
        cfg = load_config(args.config)
        cmd_pre(cfg, args.output_dir)
    elif args.command == "post":
        cfg = load_config(args.config)
        cmd_post(cfg, args.output_dir, args.resources)
    elif args.command == "compare":
        cmd_compare(args.output_dir, args.mappings)


if __name__ == "__main__":
    main()
