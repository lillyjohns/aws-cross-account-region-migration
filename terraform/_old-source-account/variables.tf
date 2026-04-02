variable "target_account_id" {
  description = "Target AWS account ID to share resources with"
  type        = string
}

variable "source_region" {
  description = "Source region"
  type        = string
  default     = "ap-southeast-1"
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
