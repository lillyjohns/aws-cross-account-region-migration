#!/usr/bin/env python3
"""S3 verify: compare source and target bucket contents."""

import argparse, boto3
from services.shared.utils import load_config, human_size


def compare_buckets(src_session, tgt_session, src_bucket, tgt_bucket):
    src_s3 = src_session.client("s3")
    tgt_s3 = tgt_session.client("s3")

    def list_all(client, bucket):
        objects = {}
        for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket):
            for obj in page.get("Contents", []):
                objects[obj["Key"]] = {"size": obj["Size"], "etag": obj["ETag"]}
        return objects

    print(f"\n🔍 Comparing s3://{src_bucket} → s3://{tgt_bucket}...")
    src_objs = list_all(src_s3, src_bucket)
    tgt_objs = list_all(tgt_s3, tgt_bucket)

    missing = set(src_objs) - set(tgt_objs)
    extra = set(tgt_objs) - set(src_objs)
    size_mismatch = [k for k in src_objs if k in tgt_objs and src_objs[k]["size"] != tgt_objs[k]["size"]]

    print(f"  Source: {len(src_objs)} objects ({human_size(sum(o['size'] for o in src_objs.values()))})")
    print(f"  Target: {len(tgt_objs)} objects ({human_size(sum(o['size'] for o in tgt_objs.values()))})")

    ok = True
    if missing:
        print(f"  ❌ Missing in target: {len(missing)}")
        for k in list(missing)[:5]:
            print(f"     - {k}")
        ok = False
    if extra:
        print(f"  ⚠️  Extra in target: {len(extra)}")
    if size_mismatch:
        print(f"  ❌ Size mismatch: {len(size_mismatch)}")
        ok = False

    if ok:
        print(f"  ✅ All {len(src_objs)} objects matched")
    return ok


def main():
    parser = argparse.ArgumentParser(description="S3 verify: compare source and target buckets")
    parser.add_argument("-c", "--config", default="scripts/config.yaml")
    args = parser.parse_args()

    cfg = load_config(args.config)
    src_session = boto3.Session(profile_name=cfg["source"]["profile"], region_name=cfg["source"]["region"])
    tgt_session = boto3.Session(profile_name=cfg["target"]["profile"], region_name=cfg["target"]["region"])

    all_ok = True
    for b in cfg["s3"]["buckets"]:
        if not compare_buckets(src_session, tgt_session, b["source"], b["target"]):
            all_ok = False

    exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
