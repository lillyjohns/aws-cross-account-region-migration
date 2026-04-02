variable "source_account_id" {
  description = "Source AWS account ID"
  type        = string
}

variable "target_region" {
  description = "Target region"
  type        = string
  default     = "ap-southeast-7"
}

variable "project_name" {
  description = "Project name for resource tagging"
  type        = string
  default     = "cross-account-migration"
}
