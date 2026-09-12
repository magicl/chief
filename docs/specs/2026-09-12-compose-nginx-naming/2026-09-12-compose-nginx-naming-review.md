# Compose nginx naming — Chief Code Review

> Agent-generated from `superpowers/requesting-code-review`. Update **Status** as findings are resolved.

**Design:** [`2026-09-12-compose-nginx-naming-design.md`](./2026-09-12-compose-nginx-naming-design.md)
**Plan:** [`2026-09-12-compose-nginx-naming-plan.md`](./2026-09-12-compose-nginx-naming-plan.md)
**Branch:** `feat/2026-09-12-compose-nginx-naming`
**Review range:** `af1b6f7957311382d71872038892283e093ac7f2..e833ef5422c5ba1bc3dab6d12b8b561f17ac0d8c` (2026-09-12)

## Assessment

**Ready to merge?** Yes, after one test assertion.

**Reasoning:** Compose behavior and current tests are correct; the existing contract suite can cheaply enforce the design boundary that only nginx services receive explicit names.

## Strengths

- Names and watch paths match slot overlays and bind-mounted configuration.
- Existing Compose tests lock exact source interpolation and watch/mount pairing.
- Application and Kubernetes files are unchanged.

## Issues

### Critical

None.

### Important

None.

### Minor

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| 1 | Rejected | `backend/chief/tests/test_compose_config.py:386-393` | Automated tests do not run rendered slot interpolation. | Docker Compose is not an established unit-test dependency; source assertions plus the plan’s rendered verification are sufficient. |
| 2 | Fixed | `backend/chief/tests/test_compose_config.py:381-393` | The design boundary that only nginx services receive `container_name` is not asserted. | `TestComposeNginxNaming` now requires the named-service set to be exactly `{chief-nginx, chief-static}`. |
| 3 | Rejected | `infra/docker/docker-compose.yml:53-80` | `nginx-conf.d` is not watched. | The directory is intentionally empty and not included by nginx; add a watch only if it becomes real configuration. |

## Recommendations

- Keep agent findings in this review artifact, not the human-locked revision file.
