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

## Project Structure

```
services/
├── ec2/
│   ├── prepare.py       # Seed marker via SSM + fingerprint
│   ├── migrate.py       # AMI create → share → copy (re-encrypt)
│   └── verify.py        # Check marker on target instance
├── s3/
│   ├── prepare.py       # Fingerprint source bucket
│   ├── migrate.py       # Sync objects with SSE-KMS
│   └── verify.py        # Compare source vs target objects
├── rds/
│   ├── prepare.py       # Seed validation row + fingerprint
│   ├── migrate.py       # Snapshot → share → copy → restore
│   └── verify.py        # Check validation row on target
└── shared/
    └── utils.py         # Config loader, wait_for, helpers
cfn/
├── source-stack.yaml    # VPC, EC2, S3, RDS, KMS, IAM
└── target-stack.yaml    # S3, KMS
scripts/
└── gen-config.sh        # Generate config.yaml from CloudFormation outputs
```

## Prerequisites

- Python >= 3.9
- AWS CLI v2
- Two AWS accounts (source and target)

## Setup

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

```bash
make infra TARGET_ACCOUNT_ID=<YOUR_TARGET_ACCOUNT_ID> DB_PASSWORD=<CHOOSE_A_PASSWORD>
```

`DB_PASSWORD` is the master password for the new test RDS instance — pick any value you like. You'll need it later for the RDS seed step.

This creates:
- **Source stack** (ap-southeast-1): VPC, EC2, S3 (SSE-KMS), RDS, KMS, IAM
- **Target stack** (ap-southeast-7): S3 (SSE-KMS), KMS

⏱ Takes ~10–15 min (mostly RDS).

### 3. Generate Config

```bash
make gen-config
```

Populates `scripts/config.yaml` from CloudFormation stack outputs — no manual editing needed.

### 4. Install Python Dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

## EC2 Migration

### Prepare

Seed a marker file on the source instance via SSM and capture a fingerprint:

```bash
make ec2-prepare
# or with a specific instance:
python3 -m services.ec2.prepare -c scripts/config.yaml -i <INSTANCE_ID>
```

Save the token printed — you'll need it for verification.

> Requires: SSM Agent running, instance IAM role with `ssm:SendCommand`

### Migrate

Creates AMI → shares with target account → copies to target region (re-encrypted with target KMS key).

```bash
make ec2-migrate-dry    # dry run
make ec2-migrate        # run (⏱ 10–30 min)
```

Outputs a target AMI ID and a `run-instances` command to launch in the target region.

### Verify

Check the marker file on the target instance:

```bash
python3 -m services.ec2.verify -c scripts/config.yaml \
  -i <TARGET_INSTANCE_ID> --token <TOKEN_FROM_PREPARE>
```

---

## S3 Migration

### Prepare

Fingerprint the source bucket (object count, sizes, ETags):

```bash
make s3-prepare
```

### Migrate

Syncs objects from source to target bucket with SSE-KMS re-encryption.

```bash
make s3-migrate-dry     # dry run
make s3-migrate         # run (⏱ depends on data size)
```

### Verify

Compare source and target bucket contents:

```bash
make s3-verify
```

---

## RDS Migration

### Prepare

Seed a validation row in the source database (runs `psql` on the EC2 instance via SSM — no direct DB access needed):

```bash
make rds-prepare DB_PASSWORD=<YOUR_DB_PASSWORD>
```

Save the token printed — you'll need it for verification.

### Migrate

Creates snapshot → shares with target account → copies to target region (re-encrypted) → restores.

```bash
make rds-migrate-dry    # dry run
make rds-migrate        # run (⏱ 30–90 min)
```

### Verify

Check the validation row on the target database (also via SSM):

```bash
python3 -m services.rds.verify -c scripts/config.yaml \
  --db-password <YOUR_DB_PASSWORD> --token <TOKEN_FROM_PREPARE> \
  --target-instance-id <TARGET_EC2_ID> --target-rds-endpoint <TARGET_RDS_ENDPOINT>
```

---

## Post-Migration Checklist

- [ ] Update Security Groups in target account
- [ ] Update IAM roles / instance profiles
- [ ] Update application connection strings (RDS endpoints)
- [ ] Update DNS records (Route 53)
- [ ] Verify data integrity
- [ ] Test application functionality
- [ ] Clean up source snapshots / AMIs after validation

## Cleanup

```bash
make destroy
```

Deletes both CloudFormation stacks and all resources.

## Security Notes

- All cross-region copies are re-encrypted with the target account's KMS key
- Source KMS key grants only `Decrypt` + `ReEncryptFrom` to target account
- IAM roles follow least-privilege for each service
- `--dry-run` flag available on all migration tools — use it first

## License

MIT
