# AWS infrastructure for defense system. Request credentials before apply.
# EKS/ECS, RDS, S3, IAM, CloudWatch, optional Prometheus/Grafana.

terraform {
  required_version = ">= 1.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
  backend "s3" {
    # Set via -backend-config or env. Do not commit bucket/key.
    # bucket = "defense-tfstate"
    # key    = "defense/terraform.tfstate"
    # region = "us-east-1"
  }
}

variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "environment" {
  type    = string
  default = "dev"
}

provider "aws" {
  region = var.aws_region
}

# Placeholder: create these resources after user provides account/region.
# resource "aws_eks_cluster" "defense" { ... }
# resource "aws_db_instance" "defense" { ... }
# resource "aws_s3_bucket" "defense_telemetry" { ... }
# resource "aws_iam_role" "defense_ecs" { ... }

output "region" {
  value = var.aws_region
}

output "environment" {
  value = var.environment
}
