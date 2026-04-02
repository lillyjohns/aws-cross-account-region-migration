#!/usr/bin/env python3
"""EC2 verify: check marker file on target + compare fingerprints."""

import argparse, hashlib, json, sys, time, boto3
from services.shared.utils import load_config

MARKER_PATH = "/tmp/migration-marker.json"


def _ssm_run(ssm, instance_id, commands, label="command"):
    resp = ssm.send_command(
        InstanceIds=[instance_id], DocumentName="AWS-RunShellScript",
        Parameters={"commands": commands}, Comment=f"migration-{label}",
    )
    cmd_id = resp["Command"]["CommandId"]
    for _ in range(30):
        time.sleep(2)
        result = ssm.get_command_invocation(CommandId=cmd_id, InstanceId=instance_id)
        if result["Status"] in ("Success", "Failed", "TimedOut", "Cancelled"):
            return result
    raise TimeoutError(f"SSM command timed out on {instance_id}")


def verify_marker(session, instance_id, expected_token):
    ssm = session.client("ssm")
    print(f"\n🔍 Verifying EC2 marker on {instance_id}...")
    result = _ssm_run(ssm, instance_id, [f"cat {MARKER_PATH}"], label="verify")

    if result["Status"] != "Success":
        print(f"  ❌ Marker file not found or SSM failed")
        return False

    try:
        marker = json.loads(result["StandardOutputContent"].strip())
    except json.JSONDecodeError:
        print(f"  ❌ Marker file exists but content is invalid")
        return False

    expected_checksum = hashlib.sha256(expected_token.encode()).hexdigest()
    if marker.get("migration_token") == expected_token and marker.get("checksum") == expected_checksum:
        print(f"  ✅ Token matched: {expected_token}")
        print(f"  ✅ Checksum matched")
        return True
    else:
        print(f"  ❌ Token mismatch: expected={expected_token}, found={marker.get('migration_token')}")
        return False


def main():
    parser = argparse.ArgumentParser(description="EC2 verify: check marker + fingerprint")
    parser.add_argument("-c", "--config", default="scripts/config.yaml")
    parser.add_argument("-i", "--instance-id", required=True, help="Target instance ID")
    parser.add_argument("--token", required=True, help="Token from prepare step")
    args = parser.parse_args()

    cfg = load_config(args.config)
    session = boto3.Session(profile_name=cfg["target"]["profile"], region_name=cfg["target"]["region"])
    ok = verify_marker(session, args.instance_id, args.token)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
