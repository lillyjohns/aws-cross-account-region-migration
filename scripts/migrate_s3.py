#!/usr/bin/env python3
"""S3 cross-account cross-region migration via sync or replication."""

import argparse, sys, yaml, boto3
from botocore.config import Config


def load_config(path):
    with open(path) as f:
        return yaml.safe_load(f)


def sync_bucket(cfg, src_bucket, tgt_bucket, prefix="", dry_run=False):
    src = cfg["source"]
    tgt = cfg["target"]
    kms_key = cfg["target_kms_key_arn"]

    src_s3 = boto3.Session(profile_name=src["profile"], region_name=src["region"]).client("s3")
    tgt_s3 = boto3.Session(profile_name=tgt["profile"], region_name=tgt["region"]).client("s3")

    # Ensure target bucket exists
    print(f"\n[1/3] Ensuring target bucket {tgt_bucket} exists in {tgt['region']}...")
    if not dry_run:
        try:
            tgt_s3.head_bucket(Bucket=tgt_bucket)
            print(f"  ✅ Bucket exists")
        except tgt_s3.exceptions.ClientError:
            tgt_s3.create_bucket(
                Bucket=tgt_bucket,
                CreateBucketConfiguration={"LocationConstraint": tgt["region"]},
            )
            print(f"  ✅ Created bucket")

    # List and copy objects
    print(f"\n[2/3] Listing objects in s3://{src_bucket}/{prefix}...")
    paginator = src_s3.get_paginator("list_objects_v2")
    pages = paginator.paginate(Bucket=src_bucket, Prefix=prefix)

    copied, skipped, errors = 0, 0, 0
    for page in pages:
        for obj in page.get("Contents", []):
            key = obj["Key"]
            # Check if target already has this object with same size
            try:
                tgt_head = tgt_s3.head_object(Bucket=tgt_bucket, Key=key)
                if tgt_head["ContentLength"] == obj["Size"]:
                    skipped += 1
                    continue
            except Exception:
                pass

            if dry_run:
                print(f"  DRY RUN: would copy {key} ({obj['Size']} bytes)")
                copied += 1
                continue

            try:
                # Download from source, upload to target with SSE-KMS
                resp = src_s3.get_object(Bucket=src_bucket, Key=key)
                tgt_s3.put_object(
                    Bucket=tgt_bucket, Key=key, Body=resp["Body"].read(),
                    ServerSideEncryption="aws:kms", SSEKMSKeyId=kms_key,
                )
                copied += 1
                if copied % 100 == 0:
                    print(f"  ... {copied} objects copied")
            except Exception as e:
                print(f"  ❌ Failed: {key}: {e}", file=sys.stderr)
                errors += 1

    print(f"\n[3/3] ✅ Sync complete: {copied} copied, {skipped} skipped, {errors} errors")
    return copied


def setup_replication(cfg, src_bucket, tgt_bucket, dry_run=False):
    """Set up S3 Cross-Region Replication (alternative to sync)."""
    src = cfg["source"]
    tgt = cfg["target"]

    src_s3 = boto3.Session(profile_name=src["profile"], region_name=src["region"]).client("s3")

    print(f"\nSetting up CRR: s3://{src_bucket} → s3://{tgt_bucket}")

    # Enable versioning on both buckets
    if not dry_run:
        src_s3.put_bucket_versioning(Bucket=src_bucket, VersioningConfiguration={"Status": "Enabled"})
        tgt_s3 = boto3.Session(profile_name=tgt["profile"], region_name=tgt["region"]).client("s3")
        tgt_s3.put_bucket_versioning(Bucket=tgt_bucket, VersioningConfiguration={"Status": "Enabled"})

    print("  ✅ Versioning enabled on both buckets")
    print("  ⚠️  To complete CRR setup, create a replication IAM role and rule:")
    print(f"    Source: s3://{src_bucket} ({src['region']})")
    print(f"    Target: s3://{tgt_bucket} ({tgt['region']})")
    print(f"    Target account: {tgt['account_id']}")
    print("  See: https://docs.aws.amazon.com/AmazonS3/latest/userguide/replication-walkthrough-2.html")


def main():
    parser = argparse.ArgumentParser(description="Migrate S3 buckets cross-account cross-region")
    parser.add_argument("-c", "--config", default="config.yaml", help="Config file path")
    parser.add_argument("-s", "--source-bucket", help="Override: source bucket name")
    parser.add_argument("-t", "--target-bucket", help="Override: target bucket name")
    parser.add_argument("-p", "--prefix", default="", help="S3 key prefix filter")
    parser.add_argument("--mode", choices=["sync", "replication"], default="sync",
                        help="sync=one-time copy, replication=setup CRR")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done")
    args = parser.parse_args()

    cfg = load_config(args.config)

    print(f"S3 Migration ({args.mode}): {cfg['source']['region']} → {cfg['target']['region']}")

    if args.source_bucket and args.target_bucket:
        buckets = [{"source": args.source_bucket, "target": args.target_bucket}]
    else:
        buckets = cfg["s3"]["buckets"]

    for b in buckets:
        try:
            if args.mode == "sync":
                sync_bucket(cfg, b["source"], b["target"], prefix=args.prefix, dry_run=args.dry_run)
            else:
                setup_replication(cfg, b["source"], b["target"], dry_run=args.dry_run)
        except Exception as e:
            print(f"\n❌ Failed: {b['source']} → {b['target']}: {e}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
