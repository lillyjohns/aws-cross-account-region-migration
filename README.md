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
```

To target a specific instance:

```bash
python3 -m services.ec2.prepare -c scripts/config.yaml -i <INSTANCE_ID>
```

Save the token printed — you'll need it for verification.

> Requires: SSM Agent running, instance IAM role with `ssm:SendCommand`

### Migrate

Migrates EC2 via AMI copy — the standard approach for cross-account, cross-region EC2 migration:

1. **Create AMI** from the source instance (snapshots all EBS volumes, no reboot)
2. **Share AMI** with the target account (grants launch permission + snapshot access)
3. **Copy AMI** to the target region — this transfers the data cross-region and re-encrypts all EBS snapshots with the target account's KMS key
4. **Launch instance** from the copied AMI in the target VPC (with SSM role attached)

Dry run:

```bash
make ec2-migrate-dry
```

Run (⏱ 10–30 min):

```bash
make ec2-migrate
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

Upload sample objects to the source bucket and fingerprint it:

```bash
make seed-s3
```

```bash
make s3-prepare
```

### Migrate

Migrates S3 via `aws s3 sync` — parallel transfers with automatic cross-account access:

1. **Grant cross-account read** — temporarily adds a bucket policy on the source bucket allowing the target account to read objects
2. **Run `aws s3 sync`** — copies new/changed objects in parallel, re-encrypting each with the target KMS key (`--sse aws:kms`)
3. **Revoke access** — removes the temporary bucket policy after sync completes

Only new or modified objects are transferred (compared by key, size, and timestamp).

Dry run:

```bash
make s3-migrate-dry
```

Run (⏱ depends on data size):

```bash
make s3-migrate
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

Migrates RDS via snapshot copy — the standard approach for cross-account, cross-region RDS migration:

1. **Create snapshot** of the source DB instance
2. **Share snapshot** with the target account (grants restore permission)
3. **Copy snapshot** to the target region — re-encrypts with the target account's KMS key
4. **Restore DB instance** from the copied snapshot in the target VPC (with target subnet group and security group)

The restored instance retains all data, schema, users, and engine configuration from the source.

Dry run:

```bash
make rds-migrate-dry
```

Run (⏱ 30–90 min):

```bash
make rds-migrate
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

Delete all migration artifacts (tagged `MigrationPOC=true`) and CloudFormation stacks:

```bash
make clean-all
```

This finds and deletes all resources created by the migration scripts (EC2 instances, AMIs, snapshots, RDS instances, RDS snapshots) in both accounts, then deletes the CloudFormation stacks.

To delete only the CloudFormation stacks (without cleaning migration artifacts):

```bash
make destroy
```

## Security Notes

- All cross-region copies are re-encrypted with the target account's KMS key
- Source KMS key grants only `Decrypt` + `ReEncryptFrom` to target account
- IAM roles follow least-privilege for each service
- `--dry-run` flag available on all migration tools — use it first

## License

MIT
