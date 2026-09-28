# Reproducibility pins introduced by independent review

`requirements.lock` is an exact-version constraints snapshot of the verified
2026-09-28 development environment. CI installs extras under these constraints;
`local-proxy` declares Paramiko and development extras declare JSON Schema and
timezone data. The Python 3.10 rpds-py exception was resolved from its compatible
wheel metadata. Platform-only/transitive dependencies absent from that environment
may still be selected by pip: this is not a complete cross-platform hash lock.

```sh
python -m pip install -c deployment/requirements.lock -e '.[dev,local-proxy]'
```

CI pins Ubuntu 24.04, uses Node 26.4.0 (the locally tested version), and uses the
Node 24 runtime generation of the official checkout/setup actions. Python minor
versions remain 3.10 and 3.12; hosted runner patch images and action major tags
are not immutable. Sources: https://github.com/actions/checkout,
https://github.com/actions/setup-python, https://github.com/actions/setup-node.

`engineering-tests.Dockerfile` pins the base RepoDigest observed on Spark and
`engineering-tests.lock` pins all installed packages observed in the existing
production test image. Build with this directory as context after supplying
all matching wheels in `deployment/wheels/`. The previous retained wheel set did
not contain every test dependency; it must be completed before an offline rebuild.
Do not replace the deployed test image merely because this recipe changed.
Existing test image: `sha256:97c359e2434c74097e224f2148ca16cf1449fc001cde393ad3c481d85a1eb111`.
Base RepoDigest: `python@sha256:e5c9fa26ffb76e11e0f054f30dc2523a2f9693f0c36c0cf1e39b27e152d899fc`.

These pins reduce drift, but a verified new image rebuild, complete wheel hash
manifest, and cross-platform hash lock remain separate follow-up work. Preserve
existing runtime/image backup evidence for exact recovery of this deployment.
