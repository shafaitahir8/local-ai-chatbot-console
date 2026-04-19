# Known Issues

Items in this file are intentionally deferred for later. These are not blocked by code changes alone or are not being addressed now because the team is not buying subscriptions or new paid infrastructure at this time.

## Deferred Items

### Query-token transport is not fully removed yet
- Current state: public bot endpoints now prefer `Authorization: Bearer <token>` and `X-Tunnel-Token`, but `?token=` still exists as a compatibility fallback.
- Why deferred: existing public embed and HubSpot-style integrations may still rely on tokenized URLs, and removing query-token support immediately would be a breaking change.
- Desired future state: disable `PUBLIC_QUERY_TOKEN_COMPAT` and remove query-token handling entirely once all clients have moved to header-based transport.

### Stable public domain remains infrastructure-dependent
- Current state: the app supports stable/public tunnel setups, but a truly stable public domain depends on external tunnel/domain infrastructure.
- Why deferred: we are not purchasing paid tunnel/custom-domain subscriptions right now.
- Examples:
  - paid ngrok reserved/custom domains
  - other subscription-backed static public domain setups
- Desired future state: move to a stable first-party or reserved public domain so embed/widget delivery does not depend on changing tunnel URLs.

### Full multi-tenant per-user bot authorization is not part of the current public model
- Current state: public bot routes are protected by bot ID plus shared public token, not by a user-specific ownership principal.
- Why deferred: the current product is designed around public bot exposure and admin ownership, not end-user identities for each public chat request.
- Desired future state: if the product evolves into a multi-tenant external-user API, add principal-bound authorization checks for each `{bot_id}` access.
