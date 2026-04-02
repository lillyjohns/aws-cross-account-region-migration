# AWS Cross-Account Cross-Region Migration

Sample tooling for migrating EC2, S3, and RDS resources between AWS accounts and regions.

**Source**: `ap-southeast-1` (Singapore) → **Target**: `ap-southeast-7` (Thailand)

## Architecture

```
Source Account (ap-southeast-1)          Target Account (ap-southeast-7)
┌──────────────────────────────┐         ┌──────────────────────────────┐
│  EC2 Instance                │         │  EC2 Instance (from AMI)     │
│    → Create AMI              │────────→│    ← Copy AMI + re-encrypt   │
│    → Share with target acct  │         │                              │
│                              │         │                              │
│  S3 Bucket (SSE-KMS CMK)     │         │  S3 Bucket (SSE-KMS CMK)    │
│    → sync / CRR              │────────→│    ← Objects re-encrypted    │
│                              │         │      with target KMS key     │
│                              │         │                              │
│  RDS Instance                │         │  RDS Instance (from snap)    │
│    → Create snapshot         │────────→│    ← Copy snap + restore     │
│    → Share with target acct  │         │    ← Re-encrypt with new key │
│                              │         │                              │
│  KMS Key (shared)            │         │  KMS Key (target-owned)      │
│  IAM Role (migration)        │         │  IAM Role (migration)        │
└──────────────────────────────┘         └──────────────────────────────┘
```

## Prerequisites

- Python >= 3.9
- AWS CLI v2
- Two AWS accounts (source and target)

## Quick Start

### 1. Configure AWS Profiles

Log into each AWS account's console, click your username (top right) → **Command line or programmatic access**, and copy the credentials into named profiles:

```bash
# Source account (Singapore)
aws configure set region ap-southeast-1 --profile source-account
aws configure set aws_access_key_id <PASTE> --profile source-account
aws configure set aws_secret_access_key <PASTE> --profile source-account
aws configure set aws_session_token <PASTE> --profile source-account

# Target account (Thailand)
aws configure set region ap-southeast-7 --profile target-account
aws configure set aws_access_key_id <PASTE> --profile target-account
aws configure set aws_secret_access_key <PASTE> --profile target-account
aws configure set aws_session_token <PASTE> --profile target-account
```

> ⚠️ Temporary credentials expire (typically 1–12 hours). Re-run the above if your session expires.

Verify:

```bash
aws sts get-caller-identity --profile source-account
aws sts get-caller-identity --profile target-account
```

### 2. Deploy Test Infrastructure

CloudFormation deploys two stacks — one per account/region:

```bash
make infra TARGET_ACCOUNT_ID=<YOUR_TARGET_ACCOUNT_ID> DB_PASSWORD=<YOUR_DB_PASSWORD>
```

This creates:
- **Source stack** (ap-southeast-1): VPC, EC2, S3 (SSE-KMS), RDS, KMS, IAM
- **Target stack** (ap-southeast-7): S3 (SSE-KMS), KMS

⏱ Takes ~10–15 min (mostly RDS).

### 3. Generate Config

```bash
make gen-config
```

This populates `scripts/config.yaml` with real resource IDs, bucket names, and KMS ARNs from CloudFormation stack outputs — no manual editing needed.

### 4. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 5. Run migrations

```bash
# Dry run first
make dry-run-all

# EC2: AMI share + copy + launch instructions
python3 scripts/migrate_ec2.py -c scripts/config.yaml

# S3: One-time sync (or --mode replication for CRR setup)
python3 scripts/migrate_s3.py -c scripts/config.yaml

# RDS: Snapshot share + copy + restore
python3 scripts/migrate_rds.py -c scripts/config.yaml
```

## CLI Reference

### migrate_ec2.py

```
usage: migrate_ec2.py [-c CONFIG] [-i INSTANCE_ID] [--dry-run]

  -c, --config        Config file path (default: config.yaml)
  -i, --instance-id   Single instance ID to migrate
  --dry-run           Show what would be done without executing
```

### migrate_s3.py

```
usage: migrate_s3.py [-c CONFIG] [-s SOURCE] [-t TARGET] [-p PREFIX] [--mode {sync,replication}] [--dry-run]

  -c, --config         Config file path (default: config.yaml)
  -s, --source-bucket  Source bucket name
  -t, --target-bucket  Target bucket name
  -p, --prefix         S3 key prefix filter
  --mode               sync (one-time copy) or replication (setup CRR)
  --dry-run            Show what would be done without executing
```

### migrate_rds.py

```
usage: migrate_rds.py [-c CONFIG] [-d DB_INSTANCE_ID] [--instance-class CLASS] [--subnet-group NAME] [--dry-run]

  -c, --config          Config file path (default: config.yaml)
  -d, --db-instance-id  Single DB instance ID to migrate
  --instance-class      Target instance class (default: db.r6g.large)
  --subnet-group        Target DB subnet group name
  --dry-run             Show what would be done without executing
```

## What Each Tool Does

| Tool | Steps | Duration |
|------|-------|----------|
| `migrate_ec2.py` | Create AMI → Share → Copy cross-region (re-encrypt) → Print launch command | 10-30 min |
| `migrate_s3.py` | Create target bucket → Sync objects with SSE-KMS | Depends on data size |
| `migrate_rds.py` | Create snapshot → Share → Copy cross-region (re-encrypt) → Restore | 30-90 min |

## Validation (Pre/Post Check)

Two levels of validation:

### Level 1: Infrastructure Fingerprint (`validate.py`)

Compares metadata (storage size, object count, engine version) — proves the container migrated correctly.

### Level 2: Data Integrity Proof (`seed.py`)

Plants actual test data on source, verifies it survives migration on target — proves the content migrated correctly.

```
SOURCE                                          TARGET
┌──────────────────────────────┐                ┌──────────────────────────────┐
│ EC2: seed marker file        │   migrate →    │ EC2: verify marker file      │
│   /tmp/migration-marker.json │   (AMI copy)   │   /tmp/migration-marker.json │
│   token: abc-123             │                │   token: abc-123 ✅          │
│                              │                │                              │
│ RDS: insert validation row   │   migrate →    │ RDS: query validation row    │
│   _migration_validation      │   (snapshot)   │   _migration_validation      │
│   token: def-456             │                │   token: def-456 ✅          │
│                              │                │                              │
│ S3: objects with ETags       │   migrate →    │ S3: compare ETags            │
│   15432 objects, 8.5 GB      │   (sync)       │   15432 objects, 8.5 GB ✅   │
└──────────────────────────────┘                └──────────────────────────────┘
```

### Full Workflow

```bash
# ── BEFORE MIGRATION ──────────────────────────────────────

# 1a. Seed EC2: create marker file via SSM
python3 scripts/seed.py seed-ec2 -c scripts/config.yaml -i i-0abc123
#  → Token: 550e8400-e29b-41d4-a716-446655440000

# 1b. Seed RDS: insert validation record
python3 scripts/seed.py seed-rds \
  --db-url "postgres://admin:pass@mydb.xxx.ap-southeast-1.rds.amazonaws.com:5432/mydb"
#  → Token: 6ba7b810-9dad-11d1-80b4-00c04fd430c8

# 1c. Fingerprint all source resources
python3 scripts/validate.py pre -c scripts/config.yaml

# ── MIGRATE ───────────────────────────────────────────────

python3 scripts/migrate_ec2.py -c scripts/config.yaml
python3 scripts/migrate_s3.py -c scripts/config.yaml
python3 scripts/migrate_rds.py -c scripts/config.yaml

# ── AFTER MIGRATION ───────────────────────────────────────

# 3a. Verify EC2: check marker file on target instance
python3 scripts/seed.py verify-ec2 -c scripts/config.yaml --target \
  -i i-0xyz789 --token 550e8400-e29b-41d4-a716-446655440000

# 3b. Verify RDS: check validation record on target DB
python3 scripts/seed.py verify-rds \
  --db-url "postgres://admin:pass@target.xxx.ap-southeast-7.rds.amazonaws.com:5432/mydb" \
  --token 6ba7b810-9dad-11d1-80b4-00c04fd430c8

# 3c. Fingerprint target resources + compare
python3 scripts/validate.py post -c scripts/config.yaml \
  -r ec2:i-0xyz789 s3:my-target-bucket rds:mydb-migrated

python3 scripts/validate.py compare \
  -m "ec2:i-0abc123=ec2:i-0xyz789" \
     "s3:my-source-bucket=s3:my-target-bucket" \
     "rds:mydb-prod=rds:mydb-migrated"
```

### What Gets Checked

| Service | Seed (before)                              | Verify (after)                             |
|---------|--------------------------------------------|--------------------------------------------|
| EC2     | Marker file via SSM with UUID + SHA256     | Read file via SSM, compare token+checksum  |
| S3      | _(uses existing objects — ETag comparison)_| Compare object count, size, sample ETags   |
| RDS     | Insert row with UUID + SHA256 + JSON data  | Query row, compare token+checksum+data     |

### Prerequisites for seed.py

- EC2: SSM Agent running, instance IAM role with `ssm:SendCommand`
- RDS: Network access from your machine (or bastion), DB credentials

## Post-Migration Checklist

- [ ] Update Security Groups in target account
- [ ] Update IAM roles / instance profiles
- [ ] Update application connection strings (RDS endpoints)
- [ ] Update DNS records (Route 53)
- [ ] Verify data integrity
- [ ] Test application functionality
- [ ] Clean up source snapshots / AMIs after validation

## Security Notes

- All cross-region copies are re-encrypted with the target account's KMS key
- Source KMS key grants only `Decrypt` + `ReEncryptFrom` to target account
- IAM roles follow least-privilege for each service
- `--dry-run` flag available on all tools — use it first

## License

MIT
