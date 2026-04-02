#!/bin/bash
set -e

# Generate config.yaml from Terraform outputs
echo "📝 Generating scripts/config.yaml from Terraform outputs..."

cd terraform/test-resources
terraform output -raw config_yaml > ../../scripts/config.yaml
cd ../..

echo "✅ scripts/config.yaml updated"
echo ""
cat scripts/config.yaml
echo ""
echo "─────────────────────────────────────────"
echo "RDS connection URL (fill in your password):"
terraform -chdir=terraform/test-resources output -raw rds_db_url 2>/dev/null || true
echo ""
