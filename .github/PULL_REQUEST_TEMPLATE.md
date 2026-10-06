## What changed

<!-- One or two sentences. -->

## Why

<!-- The problem this solves / the decision behind it. -->

## Checklist

- [ ] Tests pass locally (`uv run pytest`), or I'm relying on PR Validation
- [ ] Schema change? It's a NEW Alembic migration, backward compatible with the running version (expand/contract)
- [ ] Kubernetes change? Both overlays (`dev`, `prod`) reviewed
- [ ] No secrets, keys or project-specific IDs committed
- [ ] README / docs updated if behaviour or setup changed
