#!/usr/bin/env bash
# Configure the remotely-managed Cloudflare Tunnel for proof.beeneural.com via the Cloudflare API.
#
#   CLOUDFLARE_API_TOKEN   scoped API token: Account > Cloudflare Tunnel > Edit, Zone > DNS > Edit
#   CLOUDFLARE_ACCOUNT_ID  Cloudflare account ID
#   CLOUDFLARE_ZONE_ID     zone ID of beeneural.com
#   CLOUDFLARE_TUNNEL_ID   tunnel ID (beeproof: 48f78845-58d9-4aa2-9411-0a2ee7c0aa74)
#   TUNNEL_HOSTNAME        default proof.beeneural.com
#   TUNNEL_ORIGIN          default http://10.10.0.10:80 (internal load balancer)
#   APPLY=true             apply; otherwise only print the current configuration
#
# The tunnel TOKEN only authenticates connectors; it cannot change this configuration.
# No secret values are printed.
set -euo pipefail

: "${CLOUDFLARE_API_TOKEN:?set CLOUDFLARE_API_TOKEN}"
: "${CLOUDFLARE_ACCOUNT_ID:?set CLOUDFLARE_ACCOUNT_ID}"
: "${CLOUDFLARE_ZONE_ID:?set CLOUDFLARE_ZONE_ID}"
TUNNEL_ID="${CLOUDFLARE_TUNNEL_ID:-48f78845-58d9-4aa2-9411-0a2ee7c0aa74}"
HOSTNAME_="${TUNNEL_HOSTNAME:-proof.beeneural.com}"
ORIGIN="${TUNNEL_ORIGIN:-http://10.10.0.10:80}"
API="https://api.cloudflare.com/client/v4"
command -v jq >/dev/null || { echo "jq is required"; exit 1; }

cf() { curl -fsS -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" -H "Content-Type: application/json" "$@"; }

echo "== Tunnel"
cf "$API/accounts/$CLOUDFLARE_ACCOUNT_ID/cfd_tunnel/$TUNNEL_ID" \
  | jq '{name: .result.name, status: .result.status, config_src: .result.config_src,
         connectors: [.result.connections[]? | {colo_name, is_pending_reconnect, opened_at}]}'

echo "== Current ingress configuration"
cf "$API/accounts/$CLOUDFLARE_ACCOUNT_ID/cfd_tunnel/$TUNNEL_ID/configurations" | jq '.result.config.ingress'

echo "== DNS record for $HOSTNAME_"
cf "$API/zones/$CLOUDFLARE_ZONE_ID/dns_records?name=$HOSTNAME_" | jq '[.result[] | {id, type, content, proxied}]'

if [ "${APPLY:-false}" != "true" ]; then
  echo "APPLY is not true: no changes made."
  exit 0
fi

echo "== Setting ingress: $HOSTNAME_ -> $ORIGIN (everything else: 404)"
payload=$(jq -n --arg h "$HOSTNAME_" --arg o "$ORIGIN" '{config: {ingress: [
  {hostname: $h, service: $o, originRequest: {connectTimeout: 10, keepAliveTimeout: 90, httpHostHeader: $h}},
  {service: "http_status:404"}]}}')
cf -X PUT "$API/accounts/$CLOUDFLARE_ACCOUNT_ID/cfd_tunnel/$TUNNEL_ID/configurations" --data "$payload" \
  | jq '{success, ingress: .result.config.ingress}'

echo "== Ensuring proxied CNAME $HOSTNAME_ -> $TUNNEL_ID.cfargotunnel.com"
target="$TUNNEL_ID.cfargotunnel.com"
existing=$(cf "$API/zones/$CLOUDFLARE_ZONE_ID/dns_records?name=$HOSTNAME_" | jq -r '.result[0].id // empty')
record=$(jq -n --arg n "$HOSTNAME_" --arg c "$target" '{type: "CNAME", name: $n, content: $c, proxied: true, ttl: 1,
  comment: "BeeProof AI via Cloudflare Tunnel"}')
if [ -n "$existing" ]; then
  cf -X PUT "$API/zones/$CLOUDFLARE_ZONE_ID/dns_records/$existing" --data "$record" | jq '{success, content: .result.content}'
else
  cf -X POST "$API/zones/$CLOUDFLARE_ZONE_ID/dns_records" --data "$record" | jq '{success, content: .result.content}'
fi
echo "done"
