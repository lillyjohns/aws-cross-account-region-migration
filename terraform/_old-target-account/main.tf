terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
}

provider "aws" {
  region = var.target_region
}

data "aws_caller_identity" "current" {}

# --- KMS Key for re-encrypting migrated resources ---

resource "aws_kms_key" "migration" {
  description             = "Migration key for re-encrypting resources from source account"
  deletion_window_in_days = 30
  enable_key_rotation     = true

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "TargetAccountAdmin"
        Effect    = "Allow"
        Principal = { AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root" }
        Action    = "kms:*"
        Resource  = "*"
      }
    ]
  })

  tags = { Project = var.project_name }
}

resource "aws_kms_alias" "migration" {
  name          = "alias/${var.project_name}"
  target_key_id = aws_kms_key.migration.key_id
}

# --- IAM Role for migration scripts ---

resource "aws_iam_role" "migration" {
  name = "${var.project_name}-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { AWS = data.aws_caller_identity.current.account_id }
        Action    = "sts:AssumeRole"
      },
      {
        Effect    = "Allow"
        Principal = { AWS = var.source_account_id }
        Action    = "sts:AssumeRole"
      }
    ]
  })

  tags = { Project = var.project_name }
}

resource "aws_iam_role_policy" "migration" {
  name = "${var.project_name}-policy"
  role = aws_iam_role.migration.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "EC2Migration"
        Effect = "Allow"
        Action = [
          "ec2:CopyImage", "ec2:DescribeImages", "ec2:RunInstances",
          "ec2:CreateTags", "ec2:DescribeSubnets", "ec2:DescribeSecurityGroups"
        ]
        Resource = "*"
      },
      {
        Sid    = "S3Migration"
        Effect = "Allow"
        Action = ["s3:PutObject", "s3:ListBucket", "s3:GetBucketLocation", "s3:CreateBucket"]
        Resource = "*"
      },
      {
        Sid    = "RDSMigration"
        Effect = "Allow"
        Action = [
          "rds:CopyDBSnapshot", "rds:DescribeDBSnapshots",
          "rds:RestoreDBInstanceFromDBSnapshot", "rds:CreateDBSubnetGroup",
          "rds:DescribeDBSubnetGroups", "rds:CopyDBClusterSnapshot",
          "rds:RestoreDBClusterFromSnapshot", "rds:DescribeDBClusterSnapshots"
        ]
        Resource = "*"
      },
      {
        Sid      = "KMS"
        Effect   = "Allow"
        Action   = ["kms:Encrypt", "kms:Decrypt", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:DescribeKey", "kms:CreateGrant"]
        Resource = aws_kms_key.migration.arn
      },
      {
        Sid      = "IAMPassRole"
        Effect   = "Allow"
        Action   = "iam:PassRole"
        Resource = "*"
      }
    ]
  })
}
