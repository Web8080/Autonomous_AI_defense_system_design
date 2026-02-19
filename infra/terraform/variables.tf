variable "aws_region" {
  description = "AWS region for deployment"
  type        = string
}

variable "environment" {
  description = "Environment name (dev, staging, prod)"
  type        = string
}

variable "db_password" {
  description = "RDS master password. Use secret or TF_VAR_db_password."
  type        = string
  sensitive   = true
}

variable "jwt_secret" {
  description = "JWT signing secret for API gateway"
  type        = string
  sensitive   = true
}
