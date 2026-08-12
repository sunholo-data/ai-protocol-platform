variable "service_name" {
  type        = string
  description = "Cloud Run service name (e.g., platform-discord)."
}

variable "image" {
  type        = string
  description = "Container image URI for the channel service."
}

variable "region" {
  type        = string
  description = "GCP region for the Cloud Run service (e.g., europe-west1)."
}

variable "service_account_email" {
  type        = string
  description = "Service account the container runs as. Must have permissions for Firestore, GCS, Secret Manager, and any channel-specific APIs."
}

variable "min_instances" {
  type        = number
  description = <<-EOT
    Minimum number of instances. Set to 1+ for channels that hold a
    persistent gateway connection (Discord, IRC). 0 is fine for pure
    webhook channels (Telegram, WhatsApp, Mailgun) where Cloud Run cold
    starts are acceptable.
  EOT
  default     = 0
}

variable "max_instances" {
  type        = number
  description = "Maximum number of instances. Channels with a single gateway connection should be 1; webhook channels can scale wider."
  default     = 3
}

variable "cpu" {
  type        = string
  description = "Per-instance CPU allocation."
  default     = "1"
}

variable "memory" {
  type        = string
  description = "Per-instance memory allocation."
  default     = "512Mi"
}

variable "cpu_idle_when_unused" {
  type        = bool
  description = <<-EOT
    When true, CPU is throttled when no request is being served. Set to
    false for channels that need always-on CPU (Discord gateway heartbeat,
    streaming uploads). Defaults to false because the channel module's
    primary use case is gateway-holding services.
  EOT
  default     = false
}

variable "container_port" {
  type        = number
  description = "Container port that Cloud Run sends traffic to."
  default     = 8080
}

variable "env_vars" {
  type        = map(string)
  description = "Plain (non-secret) env vars to set in the container."
  default     = {}
}

variable "secret_env_vars" {
  type        = map(string)
  description = <<-EOT
    Map of ENV_VAR_NAME → Secret Manager secret name. The module wires
    each as a `value_source.secret_key_ref` with `version = "latest"`.
    Example:
      {
        DISCORD_TOKEN      = "discord-token"
        DISCORD_PUBLIC_KEY = "discord-public-key"
      }
  EOT
  default     = {}
}

variable "ingress" {
  type        = string
  description = "Cloud Run ingress setting. Use INGRESS_TRAFFIC_ALL for public-webhook channels (Discord, Telegram); INGRESS_TRAFFIC_INTERNAL_ONLY for internal channels."
  default     = "INGRESS_TRAFFIC_ALL"
}

variable "allow_unauthenticated" {
  type        = bool
  description = "Grant `roles/run.invoker` to allUsers. Required for public-webhook channels (Discord interaction webhook, Telegram, Mailgun). Default false — gateway-only services do not need invoker access."
  default     = false
}
