#!/usr/bin/env python3
"""S3 prepare: fingerprint source bucket."""

import argparse, hashlib, boto3
from services.shared.utils import load_config, human_size


def fingerprint(session, bucket, prefix="", sample_size=100):
    s3 = session.client("s3")
    paginator = s3.get_paginator("list_objects_v2")
    total_objects, total_size = 0, 0
    sample_objects = []

    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            total_objects += 1
            total_size += obj["Size"]
            if len(sample_objects) < sample_size:
                sample_objects.append({"key": obj["Key"], "size": obj["Size"], "etag": obj["ETag"]})

    fp = {"bucket": bucket, "total_objects": total_objects, "total_size_bytes": total_size,
          "total_size_human": human_size(total_size), "sample_objects": sample_objects}
    print(f"\n🔍 S3: {bucket}")
    print(f"  Objects: {fp['total_objects']} | Size: {fp['total_size_human']}")
    return fp


def main():
    parser = argparse.ArgumentParser(description="S3 prepare: fingerprint source bucket")
    parser.add_argument("-c", "--config", default="scripts/config.yaml")
    parser.add_argument("-s", "--source-bucket", help="Override source bucket")
    args = parser.parse_args()

    cfg = load_config(args.config)
    session = boto3.Session(profile_name=cfg["source"]["profile"], region_name=cfg["source"]["region"])
    buckets = [{"source": args.source_bucket}] if args.source_bucket else cfg["s3"]["buckets"]

    for b in buckets:
        fingerprint(session, b["source"])


if __name__ == "__main__":
    main()
