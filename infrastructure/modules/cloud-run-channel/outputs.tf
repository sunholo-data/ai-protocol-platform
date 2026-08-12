output "service_url" {
  description = "Public URL of the deployed Cloud Run service."
  value       = google_cloud_run_v2_service.channel.uri
}

output "service_name" {
  description = "Cloud Run service name (mirrors the input for convenience in module composition)."
  value       = google_cloud_run_v2_service.channel.name
}

output "service_location" {
  description = "GCP region the service runs in."
  value       = google_cloud_run_v2_service.channel.location
}
