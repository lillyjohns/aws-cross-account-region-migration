#!/bin/bash
set -e

STACK_NAME="cross-account-migration"
SOURCE_PROFILE="source-account"
TARGET_PROFILE="target-account"
SOURCE_REGION="ap-southeast-1"
TARGET_REGION="ap-southeast-7"

get_output() {
  aws cloudformation describe-stacks \
    --stack-name "${STACK_NAME}-$1" \
    --profile "$2" --region "$3" \
    --query "Stacks[0].Outputs[?OutputKey=='$4'].OutputValue" \
    --output text
}

echo "📝 Generating scripts/config.yaml from CloudFormation outputs..."

SRC_ACCOUNT=$(get_output source $SOURCE_PROFILE $SOURCE_REGION SourceAccountId)
TGT_ACCOUNT=$(get_output target $TARGET_PROFILE $TARGET_REGION TargetAccountId)
SRC_INSTANCE=$(get_output source $SOURCE_PROFILE $SOURCE_REGION SourceInstanceId)
SRC_S3=$(get_output source $SOURCE_PROFILE $SOURCE_REGION SourceS3Bucket)
TGT_S3=$(get_output target $TARGET_PROFILE $TARGET_REGION TargetS3Bucket)
TGT_KMS=$(get_output target $TARGET_PROFILE $TARGET_REGION TargetKMSKeyArn)
SRC_RDS_ID=$(get_output source $SOURCE_PROFILE $SOURCE_REGION SourceRDSIdentifier)
SRC_RDS_EP=$(get_output source $SOURCE_PROFILE $SOURCE_REGION SourceRDSEndpoint)

cat > scripts/config.yaml <<EOF
# Auto-generated from CloudFormation stack outputs
source:
  account_id: "${SRC_ACCOUNT}"
  region: "${SOURCE_REGION}"
  profile: "${SOURCE_PROFILE}"

target:
  account_id: "${TGT_ACCOUNT}"
  region: "${TARGET_REGION}"
  profile: "${TARGET_PROFILE}"

target_kms_key_arn: "${TGT_KMS}"

ec2:
  instance_ids:
    - "${SRC_INSTANCE}"

s3:
  buckets:
    - source: "${SRC_S3}"
      target: "${TGT_S3}"

rds:
  instances:
    - db_instance_id: "${SRC_RDS_ID}"
      target_instance_class: "db.t3.micro"
      target_subnet_group: ""
EOF

echo "✅ scripts/config.yaml updated"
echo ""
cat scripts/config.yaml
echo ""
echo "─────────────────────────────────────────"
echo "RDS connection URL (fill in your password):"
echo "  postgres://dbadmin:<PASSWORD>@${SRC_RDS_EP}/migrationtest"
