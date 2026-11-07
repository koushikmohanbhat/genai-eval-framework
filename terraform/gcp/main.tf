# Read-only access for genai-evals online evals, plus a scheduled Cloud Run job in the customer's own project.
terraform {
  required_version = ">= 1.5"
  required_providers {
    google = { source = "hashicorp/google", version = ">= 5.20" }
  }
}

variable "project" {
  type = string
}
variable "region" {
  type        = string
  description = "Cloud Run / Cloud Scheduler region (no default: set it in terraform.tfvars)"
}
variable "name" {
  type = string
  default = "genai-evals"
}
variable "image" {
  type = string
  description = "Artifact Registry image built from this repo's Dockerfile, containing your suites"
}
variable "schedule" {
  type = string
  default = "15 2 * * *"
}
variable "request_response_dataset" {
  type = string
  description = "BigQuery dataset holding Vertex request-response logs"
}
variable "environment" {
  type        = map(string)
  default     = {}
  description = "Extra environment variables for the eval container (the ${VAR} values your sources.yaml / providers.yaml read), e.g. AGENT_SERVICE_NAME, VERTEX_REQUEST_RESPONSE_TABLE. No secrets: use secret refs in providers.yaml."
}
variable "use_vertex_judges" {
  type = bool
  default = true
}

provider "google" {
  project = var.project
  region  = var.region
}

resource "google_service_account" "evals" {
  account_id   = var.name
  display_name = "genai-evals online (read-only)"
}

locals {
  roles = concat([
    "roles/cloudtrace.user",          # read traces (incl. spans ingested via the Telemetry API)
    "roles/logging.viewer",           # Cloud Logging / Agent Engine logs
    "roles/monitoring.viewer",        # Vertex AI / Agent Engine metrics
    "roles/monitoring.metricWriter",  # publish custom.googleapis.com/genai_eval/* scores
    "roles/bigquery.jobUser",         # run read queries
  ], var.use_vertex_judges ? ["roles/aiplatform.user"] : ["roles/aiplatform.viewer"])
}

resource "google_project_iam_member" "evals" {
  for_each = toset(local.roles)
  project  = var.project
  role     = each.value
  member   = "serviceAccount:${google_service_account.evals.email}"
}

resource "google_bigquery_dataset_iam_member" "rr" {
  dataset_id = var.request_response_dataset
  role       = "roles/bigquery.dataViewer"
  member     = "serviceAccount:${google_service_account.evals.email}"
}

resource "google_cloud_run_v2_job" "online" {
  name     = "${var.name}-online"
  location = var.region
  template {
    template {
      service_account = google_service_account.evals.email
      timeout         = "1800s"
      containers {
        image   = var.image
        command = ["genai-evals"]
        args    = ["online", "--sources", "/evals/sources.yaml", "--suite", "/evals/online_checks.yaml", "--publish", "cloud_monitoring", "--project", var.project, "--no-html"]
        dynamic "env" {
          for_each = merge(var.environment, { GCP_PROJECT = var.project })
          content {
            name  = env.key
            value = env.value
          }
        }
      }
    }
  }
}

resource "google_service_account" "scheduler" {
  account_id   = "${var.name}-sched"
  display_name = "genai-evals scheduler"
}
resource "google_cloud_run_v2_job_iam_member" "invoker" {
  name     = google_cloud_run_v2_job.online.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.scheduler.email}"
}
resource "google_cloud_scheduler_job" "online" {
  name     = "${var.name}-online"
  region   = var.region
  schedule = var.schedule
  http_target {
    http_method = "POST"
    uri         = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.project}/jobs/${google_cloud_run_v2_job.online.name}:run"
    oauth_token {
      service_account_email = google_service_account.scheduler.email
    }
  }
}

output "service_account" {
  value = google_service_account.evals.email
}
