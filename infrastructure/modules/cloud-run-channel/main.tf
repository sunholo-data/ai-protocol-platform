# Cloud Run service for a v6 channel adapter.
#
# Why this module exists separately from the generic backend service:
# channels that hold a persistent gateway connection (Discord, IRC) need
# `min_instances >= 1` so cold starts don't drop the websocket. Channels
# that are pure webhook receivers (Telegram, WhatsApp, Mailgun) can run
# with min_instances=0. Both shapes use the same image but differ in
# scaling envelope — hence one module, one knob.
#
# See: docs/design/v6.1.0/discord-channel.md §Hosting
#      docs/design/v6.1.0/channels.md
#
# Usage (sketch):
#
#   module "discord_channel" {
#     source                 = "../../modules/cloud-run-channel"
#     service_name           = "platform-discord"
#     image                  = "europe-west1-docker.pkg.dev/.../discord:latest"
#     region                 = "europe-west1"
#     min_instances          = 1     # Discord requires this for gateway keepalive
#     max_instances          = 3
#     service_account_email  = "platform@${var.project_id}.iam.gserviceaccount.com"
#     secret_env_vars = {
#       DISCORD_TOKEN          = "discord-token"
#       DISCORD_PUBLIC_KEY     = "discord-public-key"
#       DISCORD_APPLICATION_ID = "discord-application-id"
#     }
#   }

terraform {
  required_version = ">= 1.5"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 5.0"
    }
  }
}

resource "google_cloud_run_v2_service" "channel" {
  name     = var.service_name
  location = var.region
  ingress  = var.ingress

  template {
    service_account = var.service_account_email

    scaling {
      min_instance_count = var.min_instances
      max_instance_count = var.max_instances
    }

    containers {
      image = var.image

      resources {
        limits = {
          cpu    = var.cpu
          memory = var.memory
        }
        # `cpu_idle = false` keeps the CPU spinning when idle — required for
        # gateway-holding channels so the WebSocket heartbeat doesn't drop.
        # Webhook-only channels can flip this back to true via the variable.
        cpu_idle          = var.cpu_idle_when_unused
        startup_cpu_boost = true
      }

      # Plain env vars first; secret-backed entries get a `value_source`
      # via the dynamic block below.
      dynamic "env" {
        for_each = var.env_vars
        content {
          name  = env.key
          value = env.value
        }
      }

      dynamic "env" {
        for_each = var.secret_env_vars
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = env.value
              version = "latest"
            }
          }
        }
      }

      ports {
        container_port = var.container_port
      }
    }
  }

  traffic {
    type    = "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST"
    percent = 100
  }
}

# Allow unauthenticated invocations only when explicitly opted in (Discord
# slash-command webhook needs this; gateway-only services do not).
resource "google_cloud_run_v2_service_iam_member" "public" {
  count    = var.allow_unauthenticated ? 1 : 0
  name     = google_cloud_run_v2_service.channel.name
  location = google_cloud_run_v2_service.channel.location
  role     = "roles/run.invoker"
  member   = "allUsers"
}
