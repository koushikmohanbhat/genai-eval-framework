# Read-only access for genai-evals online evals, plus a scheduled job in the customer's own AWS account.
# Nothing here can modify agents, models or logs. Scores are written only as CloudWatch metrics (namespace GenAIEvals).
terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = { source = "hashicorp/aws", version = ">= 5.40" }
  }
}

variable "region" {
  type        = string
  description = "AWS region of the agent telemetry and the eval job (no default: set it in terraform.tfvars)"
}
variable "name" {
  type = string
  default = "genai-evals"
}
variable "image_uri" {
  type = string
  description = "ECR image built from this repo's Dockerfile, containing your suites"
}
variable "schedule" {
  type = string
  default = "cron(15 2 * * ? *)"
}   # daily 02:15 UTC
variable "spans_log_group" {
  type = string
  default = "aws/spans"
}
variable "invocation_log_group" {
  type = string
  default = "/aws/bedrock/modelinvocations"
}
variable "extra_log_group_arns" {
  type = list(string)
  default = []
}  # e.g. AgentCore Evaluations results
variable "agentcore_memory_arns" {
  type = list(string)
  default = []
}
variable "judge_model_arns" {
  type = list(string)
  default = []
}      # Bedrock models / inference profiles used as judges
variable "environment" {
  type        = map(string)
  default     = {}
  description = "Extra environment variables for the eval container (the ${VAR} values your sources.yaml / providers.yaml read), e.g. AGENT_SERVICE_NAME, BEDROCK_INVOCATION_LOG_GROUP. No secrets: use secret refs in providers.yaml."
}
variable "subnet_ids" {
  type = list(string)
}
variable "security_group_ids" {
  type = list(string)
}
variable "command" {
  type    = list(string)
  default = ["genai-evals", "online", "--sources", "/evals/sources.yaml", "--suite", "/evals/online_checks.yaml", "--publish", "cloudwatch", "--no-html"]
}

provider "aws" {
  region = var.region
}
data "aws_caller_identity" "me" {}

locals {
  log_arns = concat([
    "arn:aws:logs:${var.region}:${data.aws_caller_identity.me.account_id}:log-group:${var.spans_log_group}:*",
    "arn:aws:logs:${var.region}:${data.aws_caller_identity.me.account_id}:log-group:${var.invocation_log_group}:*",
  ], var.extra_log_group_arns)
}

data "aws_iam_policy_document" "read" {
  statement {
    sid       = "ReadTelemetry"
    actions   = ["logs:FilterLogEvents", "logs:GetLogEvents", "logs:StartQuery", "logs:GetQueryResults", "logs:DescribeLogGroups"]
    resources = local.log_arns
  }
  statement {
    sid       = "ReadTraces"
    actions   = ["xray:GetTraceSummaries", "xray:BatchGetTraces"]
    resources = ["*"]
  }
  statement {
    sid       = "ReadMetrics"
    actions   = ["cloudwatch:GetMetricData"]
    resources = ["*"]
  }
  dynamic "statement" {
    for_each = length(var.agentcore_memory_arns) > 0 ? [1] : []
    content {
      sid       = "ReadAgentCoreMemory"
      actions   = ["bedrock-agentcore:ListEvents", "bedrock-agentcore:GetEvent"]
      resources = var.agentcore_memory_arns
    }
  }
  dynamic "statement" {
    for_each = length(var.judge_model_arns) > 0 ? [1] : []
    content {
      sid       = "InvokeJudgeModels"
      actions   = ["bedrock:InvokeModel"]   # Converse and ConverseStream are authorised by bedrock:InvokeModel
      resources = var.judge_model_arns
    }
  }
  statement {
    sid       = "PublishScores"
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "cloudwatch:namespace"
      values   = ["GenAIEvals"]
    }
  }
}

resource "aws_iam_role" "task" {
  name = "${var.name}-task"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Service = "ecs-tasks.amazonaws.com" }, Action = "sts:AssumeRole" }] })
}
resource "aws_iam_role_policy" "read" {
  role   = aws_iam_role.task.id
  policy = data.aws_iam_policy_document.read.json
}
resource "aws_iam_role" "exec" {
  name = "${var.name}-exec"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Service = "ecs-tasks.amazonaws.com" }, Action = "sts:AssumeRole" }] })
}
resource "aws_iam_role_policy_attachment" "exec" {
  role       = aws_iam_role.exec.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_cloudwatch_log_group" "job" {
  name              = "/genai-evals/${var.name}"
  retention_in_days = 30
}
resource "aws_ecs_cluster" "this" {
  name = var.name
}
resource "aws_ecs_task_definition" "online" {
  family                   = "${var.name}-online"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 1024
  memory                   = 2048
  task_role_arn            = aws_iam_role.task.arn
  execution_role_arn       = aws_iam_role.exec.arn
  container_definitions = jsonencode([{
    name             = "genai-evals"
    image            = var.image_uri
    command          = var.command
    essential        = true
    environment      = [for k, v in merge(var.environment, { AWS_REGION = var.region }) : { name = k, value = v }]
    logConfiguration = { logDriver = "awslogs", options = { awslogs-group = aws_cloudwatch_log_group.job.name, awslogs-region = var.region, awslogs-stream-prefix = "online" } }
  }])
}

resource "aws_iam_role" "events" {
  name = "${var.name}-events"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Service = "events.amazonaws.com" }, Action = "sts:AssumeRole" }] })
}
resource "aws_iam_role_policy" "events" {
  role = aws_iam_role.events.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = "ecs:RunTask", Resource = aws_ecs_task_definition.online.arn },
    { Effect = "Allow", Action = "iam:PassRole", Resource = [aws_iam_role.task.arn, aws_iam_role.exec.arn] },
  ] })
}
resource "aws_cloudwatch_event_rule" "schedule" {
  name                = "${var.name}-online"
  schedule_expression = var.schedule
}
resource "aws_cloudwatch_event_target" "run" {
  rule     = aws_cloudwatch_event_rule.schedule.name
  arn      = aws_ecs_cluster.this.arn
  role_arn = aws_iam_role.events.arn
  ecs_target {
    task_definition_arn = aws_ecs_task_definition.online.arn
    launch_type         = "FARGATE"
    network_configuration {
      subnets          = var.subnet_ids
      security_groups  = var.security_group_ids
      assign_public_ip = false
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "privacy" {
  alarm_name          = "${var.name}-pii-echo"
  namespace           = "GenAIEvals"
  metric_name         = "Score"
  dimensions          = { Suite = "claims-agent-online", Target = "claims-agent-aws", Grader = "no_pii_echo" }
  statistic           = "Minimum"
  period              = 86400
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_description   = "Production sessions echoed personal identifiers (genai-evals online)"
}

output "task_role_arn" {
  value = aws_iam_role.task.arn
}
