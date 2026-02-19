# Dashboard on AWS - placeholders. Fill in and uncomment when deploying.
# Request credentials and approve before apply. CORS_ORIGINS on API must include dashboard origin.

# ECR repository for dashboard Docker image (push with docker tag + push)
# resource "aws_ecr_repository" "dashboard" {
#   name                 = "defense-dashboard"
#   image_tag_mutability = "MUTABLE"
#   image_scanning_configuration {
#     scan_on_push = true
#   }
# }

# Optional: S3 + CloudFront for static export (if using output = "export" in next.config.js)
# resource "aws_s3_bucket" "dashboard_static" {
#   bucket = "defense-dashboard-${var.environment}-${data.aws_caller_identity.current.account_id}"
# }
# resource "aws_s3_bucket_website_configuration" "dashboard_static" {
#   bucket = aws_s3_bucket.dashboard_static.id
#   index_document { suffix = "index.html" }
#   error_document { key = "404.html" }
# }

# ECS task definition placeholder: use image from ECR, set NEXT_PUBLIC_API_URL to API gateway URL.
# ALB + target group + listener to expose the dashboard. Route53 record optional.

# data "aws_caller_identity" "current" {}

# outputs
# output "dashboard_ecr_url" {
#   value = aws_ecr_repository.dashboard.repository_url
# }
