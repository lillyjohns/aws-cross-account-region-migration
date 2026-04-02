"""Shared utilities for migration services."""

import time, yaml

MIGRATION_TAG = {"Key": "MigrationPOC", "Value": "true"}


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


def human_size(nbytes):
    for unit in ["B", "KB", "MB", "GB", "TB", "PB"]:
        if nbytes < 1024:
            return f"{nbytes:.2f} {unit}"
        nbytes /= 1024
    return f"{nbytes:.2f} EB"
