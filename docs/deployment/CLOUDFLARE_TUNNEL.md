# Cloudflare Tunnel

Tunnel: **beeproof**, ID `48f78845-58d9-4aa2-9411-0a2ee7c0aa74`. It is a remotely managed tunnel:
created in the dashboard and authenticated with a token.

## Why a VM connector

Cloud Run cannot host a persistent `cloudflared` connector reliably: instances scale to zero and receive no
inbound traffic. Cloud Run services also cannot be reached directly on an internal address. The supported
path is therefore:

```
Cloudflare edge → Tunnel → cloudflared on GCE (no public IP) → internal ALB 10.10.0.10:80 → serverless NEGs → Cloud Run
```

The internal ALB does the path routing: `/api`, `/api/*` → `beeproof-api`, everything else →
`beeproof-web`. The worker has no route. Cloud Run ingress is `internal`, so the `run.app` URLs are not
reachable from the internet.

## Route to configure (one-time)

**Dashboard:** Zero Trust → Networks → Tunnels → beeproof → Public Hostname → Add:

| Field | Value |
|---|---|
| Subdomain | `proof` |
| Domain | `beeneural.com` |
| Path | *(empty)* |
| Service | `HTTP` → `10.10.0.10:80` |
| Additional settings › HTTP Host Header | `proof.beeneural.com` |

Cloudflare creates the proxied CNAME `proof → 48f78845-58d9-4aa2-9411-0a2ee7c0aa74.cfargotunnel.com`.
No other public hostname is needed.

**Or via API:** set the `CLOUDFLARE_API_TOKEN` secret and the `CLOUDFLARE_ACCOUNT_ID` / `CLOUDFLARE_ZONE_ID` /
`CLOUDFLARE_TUNNEL_ID` variables. Run **Configure Cloudflare tunnel route** with `apply=false` to inspect,
then with `apply=true` to apply. Locally: `APPLY=true bash scripts/cloudflare/configure-tunnel.sh`.

**The tunnel token cannot change routes or DNS.** It only lets a connector join the tunnel. Configuration
changes need a scoped API token or the dashboard.

## Token handling

- Store it only as the GitHub secret `CLOUDFLARE_TUNNEL_TOKEN`. **Sync Cloudflare tunnel token** copies it into
  Secret Manager (`beeproof-cloudflare-tunnel-token`).
- The deployer identity can add secret versions but cannot read them. Only the connector VM's service account
  can read the token.
- The VM writes it to `/etc/cloudflared/tunnel.env` (root, mode 0600). systemd passes it to the unprivileged
  `cloudflared` user. It is never logged.
- The token was pasted into a chat session during setup. If that transcript is or may become visible to
  anyone else, **rotate it**: dashboard → tunnel → Configure → Refresh token, then update the GitHub secret,
  re-run the sync workflow, and reset the VM.

## Connector operations

| Task | Command |
|---|---|
| Status (IAP SSH, `enable_iap_ssh=true`) | `gcloud compute ssh beeproof-tunnel-1 --zone asia-south1-a --tunnel-through-iap -- 'systemctl status cloudflared --no-pager; curl -fsS 127.0.0.1:20241/ready'` |
| Logs | `gcloud logging read 'resource.type="gce_instance" AND textPayload:"cloudflared"' --limit 50` |
| Pick up a rotated token | `gcloud compute instances reset beeproof-tunnel-1 --zone asia-south1-a` |
| High availability | `connector_count = 2` in tfvars: a second VM in `asia-south1-b` joins the same tunnel |

Reliability controls:
- systemd `Restart=always`, with hardening: `NoNewPrivileges`, `ProtectSystem=strict`, no capabilities.
- Outbound-only networking through Cloud NAT. No ingress firewall rules, except optional IAP SSH.
- Shielded VM, OS Login, project SSH keys blocked.
- A readiness check every minute raises the `beeproof/tunnel_not_ready` alert.
- Also enable Cloudflare's own notification "Tunnel health alert" for `beeproof`.

## Recommended Cloudflare settings

- SSL/TLS: Full. The origin hop inside GCP is plain HTTP over the private VPC.
- WAF managed rules on. Add a rate-limiting rule on `POST /api/v1/*`, for example 60 requests per minute per
  IP: the application has no authentication.
- Optional: Cloudflare Access in front of the hostname to restrict who can use it.
