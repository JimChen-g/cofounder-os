# CoFounder OS Deployment Workflow

## Direction

Deployment is one-way:

```text
Mac Git repository
    -> verified Git Bundle
    -> rsync over dedicated SSH key
    -> DGX Spark Git checkout
```

The Mac repository is the source of truth. DGX Spark is a deployment target.

## Protected Runtime State

Deployment never runs `git clean` and does not delete untracked runtime state.

The following paths remain outside the deployment contract:

- `.env`
- `.env.local`
- `.env.backup.*`
- `.venv-vllm` or `.venv-vllm/`
- `logs/`
- `data/.locks/`
- `data/runs/`
- Model weights
- Gateway keys
- systemd and launchd configuration

## Deployment

Validation only:

```zsh
scripts/deploy-to-spark.sh --dry-run
```

Apply the current clean `main` commit:

```zsh
scripts/deploy-to-spark.sh
```

The deployment script:

1. Requires a clean local `main` branch.
2. Requires a clean remote Git working tree outside protected runtime locks.
3. Creates and verifies a local Git Bundle.
4. Transfers the bundle with `rsync` over the dedicated SSH key.
5. Creates a remote rollback bundle and manifest.
6. Fetches the Mac `main` ref from the bundle.
7. Resets the DGX checkout to the exact Mac commit.
8. Runs remote status, health, and smoke tests.
9. Automatically restores the previous remote commit if validation fails.

## Smoke Test

```zsh
scripts/smoke-product.sh
```

This tests both Mac tunnel access and direct DGX service access.

## Rollback

Rollback to the previous deployment:

```zsh
scripts/rollback-product.sh
```

Rollback to a specific known commit:

```zsh
scripts/rollback-product.sh <commit-sha>
```

Rollback never changes model weights, Gateway configuration, service units, or
the SSH tunnel.
