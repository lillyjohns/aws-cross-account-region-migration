#!/usr/bin/env python3
"""S3 cross-account cross-region migration via sync."""

import argparse, sys, boto3
from services.shared.utils import load_config


def sync_bucket(cfg, src_bucket, tgt_bucket, prefix="", dry_run=False):
    src, tgt = cfg["source"], cfg["target"]
    kms_key = cfg["target_kms_key_arn"]
    src_s3 = boto3.Session(profile_name=src["profile"], region_name=src["region"]).client("s3")
    tgt_s3 = boto3.Session(profile_name=tgt["profile"], region_name=tgt["region"]).client("s3")

    print(f"\n[1/2] Listing objects in s3://{src_bucket}/{prefix}...")
    paginator = src_s3.get_paginator("list_objects_v2")
    copied, skipped, errors = 0, 0, 0

    for page in paginator.paginate(Bucket=src_bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
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
                resp = src_s3.get_object(Bucket=src_bucket, Key=key)
                tgt_s3.put_object(Bucket=tgt_bucket, Key=key, Body=resp["Body"].read(),
                                  ServerSideEncryption="aws:kms", SSEKMSKeyId=kms_key)
                copied += 1
                if copied % 100 == 0:
                    print(f"  ... {copied} objects copied")
            except Exception as e:
                print(f"  ❌ Failed: {key}: {e}", file=sys.stderr)
                errors += 1

    print(f"\n[2/2] ✅ Sync complete: {copied} copied, {skipped} skipped, {errors} errors")


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
