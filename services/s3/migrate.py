#!/usr/bin/env python3
"""S3 cross-account cross-region migration via aws s3 sync."""

import argparse, json, subprocess, sys, boto3
from services.shared.utils import load_config


def grant_cross_account_read(cfg, src_bucket):
    """Add bucket policy granting target account read access."""
    src = cfg["source"]
    tgt = cfg["target"]
    src_s3 = boto3.Session(profile_name=src["profile"], region_name=src["region"]).client("s3")

    policy = {
        "Version": "2012-10-17",
        "Statement": [{
            "Sid": "CrossAccountMigrationRead",
            "Effect": "Allow",
            "Principal": {"AWS": f"arn:aws:iam::{tgt['account_id']}:root"},
            "Action": ["s3:GetObject", "s3:ListBucket"],
            "Resource": [f"arn:aws:s3:::{src_bucket}", f"arn:aws:s3:::{src_bucket}/*"],
        }]
    }

    print(f"  Granting read access to account {tgt['account_id']}...")
    src_s3.put_bucket_policy(Bucket=src_bucket, Policy=json.dumps(policy))
    print(f"  ✅ Bucket policy applied")


def revoke_cross_account_read(cfg, src_bucket):
    """Remove the cross-account bucket policy."""
    src = cfg["source"]
    src_s3 = boto3.Session(profile_name=src["profile"], region_name=src["region"]).client("s3")
    src_s3.delete_bucket_policy(Bucket=src_bucket)
    print(f"  ✅ Bucket policy removed")


def sync_bucket(cfg, src_bucket, tgt_bucket, prefix="", dry_run=False):
    tgt = cfg["target"]
    kms_key = cfg["target_kms_key_arn"]

    print(f"\n[1/3] Granting cross-account access on {src_bucket}...")
    grant_cross_account_read(cfg, src_bucket)

    print(f"\n[2/3] Syncing s3://{src_bucket}/{prefix} → s3://{tgt_bucket}/{prefix}...")
    src_path = f"s3://{src_bucket}/{prefix}" if prefix else f"s3://{src_bucket}"
    tgt_path = f"s3://{tgt_bucket}/{prefix}" if prefix else f"s3://{tgt_bucket}"

    cmd = [
        "aws", "s3", "sync", src_path, tgt_path,
        "--sse", "aws:kms", "--sse-kms-key-id", kms_key,
        "--profile", tgt["profile"], "--region", tgt["region"],
    ]
    if dry_run:
        cmd.append("--dryrun")

    print(f"  Running: {' '.join(cmd)}")
    result = subprocess.run(cmd)

    if result.returncode != 0:
        print(f"  ❌ Sync failed with exit code {result.returncode}", file=sys.stderr)
        sys.exit(1)

    print(f"\n[3/3] Revoking cross-account access...")
    revoke_cross_account_read(cfg, src_bucket)

    print(f"  ✅ Sync complete")


def main():
    parser = argparse.ArgumentParser(description="Migrate S3 buckets cross-account cross-region")
    parser.add_argument("-c", "--config", default="scripts/config.yaml")
    parser.add_argument("-p", "--prefix", default="", help="S3 key prefix filter")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    cfg = load_config(args.config)
    print(f"S3 Migration: {cfg['source']['region']} → {cfg['target']['region']}")

    for b in cfg["s3"]["buckets"]:
        try:
            sync_bucket(cfg, b["source"], b["target"], prefix=args.prefix, dry_run=args.dry_run)
        except Exception as e:
            print(f"\n❌ Failed: {b['source']} → {b['target']}: {e}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
