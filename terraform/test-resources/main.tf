terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
}

provider "aws" {
  region  = var.source_region
  profile = "source-account"
  alias   = "source"
}

provider "aws" {
  region  = var.target_region
  profile = "target-account"
  alias   = "target"
}

data "aws_caller_identity" "source" { provider = aws.source }
data "aws_availability_zones" "source" { provider = aws.source }

# ── VPC (source) ────────────────────────────────────────────

resource "aws_vpc" "source" {
  provider             = aws.source
  cidr_block           = "10.0.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = "${var.project_name}-source-vpc" }
}

resource "aws_subnet" "source" {
  count             = 2
  provider          = aws.source
  vpc_id            = aws_vpc.source.id
  cidr_block        = "10.0.${count.index}.0/24"
  availability_zone = data.aws_availability_zones.source.names[count.index]
  tags              = { Name = "${var.project_name}-source-subnet-${count.index}" }
}

resource "aws_internet_gateway" "source" {
  provider = aws.source
  vpc_id   = aws_vpc.source.id
  tags     = { Name = "${var.project_name}-source-igw" }
}

resource "aws_route_table" "source" {
  provider = aws.source
  vpc_id   = aws_vpc.source.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.source.id
  }
}

resource "aws_route_table_association" "source" {
  count          = 2
  provider       = aws.source
  subnet_id      = aws_subnet.source[count.index].id
  route_table_id = aws_route_table.source.id
}

resource "aws_security_group" "source" {
  provider = aws.source
  vpc_id   = aws_vpc.source.id
  name     = "${var.project_name}-source-sg"

  ingress {
    from_port = 5432
    to_port   = 5432
    protocol  = "tcp"
    self      = true
  }
  ingress {
    from_port = 3306
    to_port   = 3306
    protocol  = "tcp"
    self      = true
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${var.project_name}-source-sg" }
}

# ── KMS (source — shared with target) ──────────────────────

resource "aws_kms_key" "source" {
  provider                = aws.source
  description             = "${var.project_name} - shared with target"
  deletion_window_in_days = 7
  enable_key_rotation     = true

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "SourceAdmin"
        Effect    = "Allow"
        Principal = { AWS = "arn:aws:iam::${data.aws_caller_identity.source.account_id}:root" }
        Action    = "kms:*"
        Resource  = "*"
      },
      {
        Sid       = "TargetDecrypt"
        Effect    = "Allow"
        Principal = { AWS = "arn:aws:iam::${var.target_account_id}:root" }
        Action    = ["kms:Decrypt", "kms:DescribeKey", "kms:CreateGrant", "kms:ReEncryptFrom"]
        Resource  = "*"
      }
    ]
  })
}

# ── KMS (target — for re-encryption) ───────────────────────

resource "aws_kms_key" "target" {
  provider                = aws.target
  description             = "${var.project_name} - target re-encryption"
  deletion_window_in_days = 7
  enable_key_rotation     = true
}

# ── IAM Role for EC2 (SSM access) ──────────────────────────

resource "aws_iam_role" "ec2_ssm" {
  provider = aws.source
  name     = "${var.project_name}-ec2-ssm"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "ec2_ssm" {
  provider   = aws.source
  role       = aws_iam_role.ec2_ssm.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "ec2_ssm" {
  provider = aws.source
  name     = "${var.project_name}-ec2-ssm"
  role     = aws_iam_role.ec2_ssm.name
}

# ── EC2 Instance ────────────────────────────────────────────

data "aws_ami" "al2023" {
  provider    = aws.source
  most_recent = true
  owners      = ["amazon"]
  filter {
    name   = "name"
    values = ["al2023-ami-*-x86_64"]
  }
  filter {
    name   = "state"
    values = ["available"]
  }
}

resource "aws_instance" "source" {
  provider                    = aws.source
  ami                         = data.aws_ami.al2023.id
  instance_type               = "t3.micro"
  subnet_id                   = aws_subnet.source[0].id
  vpc_security_group_ids      = [aws_security_group.source.id]
  iam_instance_profile        = aws_iam_instance_profile.ec2_ssm.name
  associate_public_ip_address = true

  root_block_device {
    volume_size = 10
    volume_type = "gp3"
    encrypted   = true
    kms_key_id  = aws_kms_key.source.arn
  }

  user_data = <<-EOF
    #!/bin/bash
    echo "Migration test instance - $(date)" > /home/ec2-user/README.txt
    echo '{"created":"source","region":"${var.source_region}"}' > /home/ec2-user/instance-info.json
  EOF

  tags = { Name = "${var.project_name}-source-ec2" }
}

# ── S3 Bucket + Sample Objects ──────────────────────────────

resource "aws_s3_bucket" "source" {
  provider      = aws.source
  bucket_prefix = "${var.project_name}-src-"
  force_destroy = true
  tags          = { Name = "${var.project_name}-source-s3" }
}

resource "aws_s3_bucket_versioning" "source" {
  provider = aws.source
  bucket   = aws_s3_bucket.source.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket" "target" {
  provider      = aws.target
  bucket_prefix = "${var.project_name}-tgt-"
  force_destroy = true
  tags          = { Name = "${var.project_name}-target-s3" }
}

resource "aws_s3_object" "samples" {
  for_each = {
    "data/sample1.json" = jsonencode({ id = 1, name = "Alice", value = 42 })
    "data/sample2.json" = jsonencode({ id = 2, name = "Bob", value = 99 })
    "data/sample3.json" = jsonencode({ id = 3, name = "Charlie", value = 7 })
    "docs/readme.txt"   = "Migration test data - do not delete"
    "logs/test.log"     = "2026-04-01 INFO Migration test log entry"
  }

  provider = aws.source
  bucket   = aws_s3_bucket.source.id
  key      = each.key
  content  = each.value
}

# ── RDS Instance ────────────────────────────────────────────

resource "aws_db_subnet_group" "source" {
  provider   = aws.source
  name       = "${var.project_name}-source"
  subnet_ids = aws_subnet.source[*].id
}

resource "aws_db_instance" "source" {
  provider                = aws.source
  identifier              = "${var.project_name}-source-db"
  engine                  = var.db_engine
  engine_version          = var.db_engine == "postgres" ? "16.4" : "8.0.39"
  instance_class          = "db.t3.micro"
  allocated_storage       = 20
  storage_type            = "gp3"
  storage_encrypted       = true
  kms_key_id              = aws_kms_key.source.arn
  db_name                 = "migrationtest"
  username                = "admin"
  password                = var.db_password
  db_subnet_group_name    = aws_db_subnet_group.source.name
  vpc_security_group_ids  = [aws_security_group.source.id]
  skip_final_snapshot     = true
  backup_retention_period = 1
  publicly_accessible     = false

  tags = { Name = "${var.project_name}-source-rds" }
}
