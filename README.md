# proxy-ai

Pass-through proxy from Claude Code (Anthropic API) and OpenAI-compatible clients to OpenRouter.
Rules in `config.json` override the model, provider, and effort of each request. The proxy stores no keys; it forwards the client's headers as-is.

## Run

Image: [`agnusdei1207/proxy-ai`](https://hub.docker.com/r/agnusdei1207/proxy-ai) (amd64, arm64). Set `environment` in `docker-compose.yml`, then:

```bash
docker compose up -d
```

- `MODEL`, `PROVIDER`: comma-separated, tried in order. `EFFORT`: see below. Applied to every request.
- If none are set, the `config.json` rules below are used instead.

## Configuration (`config.json`)

Key = incoming model name (exact match first, then `*` wildcards from top to bottom). Value = fields to overwrite in the request body.
Edits take effect immediately (no restart needed).

```json
{
  "claude-*": {
    "model": ["deepseek/deepseek-v4.1-flash"],
    "provider": ["baidu", "streamlake/fp8"],
    "effort": "high"
  },
  "*": { "provider": { "allow_fallbacks": true } }
}
```

- `model`: OpenRouter model ID. An array is a fallback order, tried first to last (`models`)
- `provider`: an array (or string) tries only the listed providers, in order (`{"order": [...], "allow_fallbacks": false}`).
  An object is passed through as [OpenRouter provider routing](https://openrouter.ai/docs/features/provider-routing)
- `effort`: `none` | `low` | `medium` | `high`, converted per endpoint
  (`/v1/messages` → `output_config.effort`, `none` disables `thinking` / `/v1/chat/completions` → `reasoning.effort`)
- Any other field overwrites the request body as-is

## Retries

The proxy retries 429 / 5xx / connection failures automatically (waits 1, 2, 4, 8, 16 seconds; `Retry-After` wins).
After `RETRIES` attempts (default 5) it returns the last error unchanged. 400, 401, 402, 404 and similar return immediately.

## Claude Code

```powershell
$env:ANTHROPIC_BASE_URL="http://localhost:8787"        # route to this proxy
$env:ANTHROPIC_AUTH_TOKEN=$env:OPENROUTER_API_KEY      # OpenRouter key
$env:ANTHROPIC_API_KEY=""                              # empty on purpose: don't send an existing Anthropic key
claude
```

The `claude-*` model names Claude Code sends are replaced by the rules above, so no model variables are needed.
For non-Anthropic models, the `system` messages Claude Code inserts mid-conversation are moved to the top-level `system` for compatibility.
OpenAI-compatible clients use the base URL `http://localhost:8787/v1`.
