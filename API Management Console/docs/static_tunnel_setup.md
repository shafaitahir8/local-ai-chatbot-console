# Static Public Endpoint Setup (HubSpot-Safe)

This project supports static, restart-safe public endpoints using:

- Cloudflare Named Tunnels (recommended)
- ngrok Reserved Domains

The objective is to keep HubSpot module URLs stable and avoid broken embeds when tunnels restart.

## 1) One-Command `.env` Configuration

Use the helper script to configure your local environment safely.

### Cloudflare Named Tunnel

```powershell
.\scripts\configure_static_tunnel.ps1 `
  -Provider cloudflare `
  -CloudflareTunnelToken "YOUR_CLOUDFLARE_TUNNEL_TOKEN" `
  -CloudflarePublicBaseUrl "https://chat.example.com" `
  -BotId "3027bcaf-eeaf-4746-8d55-0390d9f922e6"
```

### ngrok Reserved Domain

```powershell
.\scripts\configure_static_tunnel.ps1 `
  -Provider ngrok `
  -NgrokAuthtoken "YOUR_NGROK_AUTHTOKEN" `
  -NgrokStaticDomain "your-domain.ngrok-free.app" `
  -BotId "3027bcaf-eeaf-4746-8d55-0390d9f922e6"
```

After running either command, restart:

```batch
.\start_console.bat
```

## 2) How Static URLs Are Kept Stable

- `TUNNEL_PROVIDER=cloudflare` + `CLOUDFLARE_TUNNEL_TOKEN` + `CLOUDFLARE_PUBLIC_BASE_URL`
  uses a named tunnel with a fixed URL.
- `TUNNEL_PROVIDER=ngrok` + `NGROK_AUTHTOKEN` + `NGROK_STATIC_DOMAIN`
  uses ngrok with a reserved domain.

The app automatically exposes:

- `https://YOUR-STATIC-DOMAIN/api/bot/{BOT_ID}/chat`
- `https://YOUR-STATIC-DOMAIN/embed/{BOT_ID}`

## 3) HubSpot Endpoint Pattern

Use token-protected URLs in HubSpot:

```text
https://YOUR-STATIC-DOMAIN/api/bot/{BOT_ID}/chat?token={TUNNEL_SHARED_SECRET}
https://YOUR-STATIC-DOMAIN/embed/{BOT_ID}?token={TUNNEL_SHARED_SECRET}
```

## 4) Security Best Practices

1. Keep `TUNNEL_API_ONLY=true` to minimize exposed routes.
2. Keep `TUNNEL_SHARED_SECRET` enabled and include `?token=...` in hardcoded URLs.
3. Rotate `TUNNEL_SHARED_SECRET` on a regular schedule.
4. Restrict `CORS_ORIGINS` in production (avoid `*`).
5. Use strong secrets for `ADMIN_PASSWORD`, `JWT_SECRET`, and `ENCRYPTION_SECRET`.
6. Treat HubSpot module code as public: never hardcode provider API keys there.

## 5) Troubleshooting

- No public URL:
  - Confirm tunnel provider CLI is installed and in PATH.
  - Confirm credentials/domain are valid.
- URL changes unexpectedly:
  - You are in quick-tunnel mode. Switch to Cloudflare named tunnel or ngrok reserved domain.
- HubSpot gets `403 Forbidden`:
  - Missing/invalid `token` query value (or `X-Tunnel-Token` header).
