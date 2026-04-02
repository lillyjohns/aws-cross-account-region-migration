output "kms_key_arn" {
  value = aws_kms_key.migration.arn
}

output "migration_role_arn" {
  value = aws_iam_role.migration.arn
}
