# Optional AI health checks

The development Compose file keeps Ollama outside both the default `dev` and
`test` stacks. Start it only when needed with:

```sh
docker compose -f docker-compose.dev.yml --profile ai up -d ollama
```

Run the HTTP checks from the host with
`platform/deploy/test/ai-health-check.sh`. Ollama is checked through its
`/api/tags` endpoint. To check a separately managed OpenClaw-compatible endpoint,
set `OPENCLAW_BASE_URL`; it is not contacted otherwise. Its public `/health`
endpoint is requested without credentials. The checker never prints request
headers or token values.

Generation smoke is optional. Set `AI_SMOKE_MODEL` to choose a model explicitly,
or let the checker select the first model returned by Ollama's tags API. If
neither is available, generation is skipped. An unhealthy endpoint or failed
generation is reported as optional and exits successfully, so it cannot block
the default development or test stack.

Run the local stub-based checks with:

```sh
bash platform/deploy/test/test-ai-health-check.sh
```
