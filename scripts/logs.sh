#!/usr/bin/env bash
# Stream backend logs, filtering out OTEL/telemetry noise
tail -f /tmp/backend.log 2>/dev/null | grep -v \
  -e "opentelemetry" \
  -e "otlp" \
  -e "telemetry" \
  -e "urllib3" \
  -e "MaxRetry" \
  -e "ConnectionError" \
  -e "Connection refused" \
  -e "Failed to export" \
  -e "console.developers" \
  -e "activationUrl" \
  -e "ailang-dev" \
  -e "_stored_first_result" \
  -e "Traceback" \
  -e "raise err" \
  -e "sock.connect" \
  -e "endheaders" \
  -e "send_output" \
  -e "File \"/Users"
