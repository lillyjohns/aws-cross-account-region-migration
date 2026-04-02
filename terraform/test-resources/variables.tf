variable "source_region" {
  default = "ap-southeast-1"
}

variable "target_region" {
  default = "ap-southeast-7"
}

variable "target_account_id" {
  description = "Target AWS account ID for cross-account sharing"
  type        = string
}

variable "project_name" {
  default = "cross-account-migration"
}

variable "db_password" {
  description = "RDS master password"
  type        = string
  sensitive   = true
}

variable "db_engine" {
  description = "RDS engine: postgres or mysql"
  default     = "postgres"
}
