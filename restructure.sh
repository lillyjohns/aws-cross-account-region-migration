#!/bin/bash
set -e
cd "$(dirname "$0")"

# Remove old flat scripts (replaced by services/)
rm -f scripts/migrate_ec2.py scripts/migrate_s3.py scripts/migrate_rds.py
rm -f scripts/seed.py scripts/validate.py

git add -A
git commit -m "Restructure: per-service modules (EC2, S3, RDS) with prepare/migrate/verify"
echo "✅ Done. Run: git push origin main"
