# UI correction release — September 28, 2026

The Chinese portal is now reproducible from `tools/local_portal`; the local adapter serves the decision workspace directly from `app/ui/static`, without runtime string replacements. Both retain their existing workflow boundaries and share paper/ink styling, Chinese primary navigation, and a pure presentation state helper.

Corrections include expired/failed/approved state parity; terminal polling stops; unchanged candidates preserve code, logs and feedback focus; engineering navigation clears the persisted decision-workspace ID; same-model/separate-context review wording; engineering workflow scores are inapplicable; synthetic demonstrations and T28 protocol observations are separate. T28 has zero of ten multi-valid-choice cases and supports no quality-uplift claim.

The decision panel precedes code, public version credentials are folded, attempts show persisted values, and unknown repair windows/token balances remain unknown. Existing request IDs and receipts permit read-only reconciliation; no POST is replayed. Backend business state, model configuration, historical candidates and approvals are unchanged.

Validation before release: 706 backend tests, 20 local-adapter tests, 22 shared-state assertions, Ruff, Mypy (89 files), frontend syntax and source/wheel builds. Browser checks use actual production records read-only and an explicitly labelled isolated fixture for pending approval, polling, offline and uncertain-response scenarios. Over 60 seconds the fixture's code/check/review regions had zero mutations, with expanded logs and feedback focus retained. Unknown-response approve/create each recorded one POST, then resolved by reading the persisted receipt/request ID.

Limits: retain two workspaces; full shell migration, per-phase timestamp API, repair feasibility API, and HTTP inventory API are deferred. Native `/ui/engineering` on the backend remains the earlier diagnostic interface; the supported founder entry is the loopback portal, which maps that route to the new Chinese workspace. Deep raw evidence retains its original language. Native confirm remains, now with human-readable version/expiry text. Full screen-reader and subjective owner usability acceptance are not claimed. Previous security/semantic-review limits remain.

Run locally as described in `tools/local_portal/README.md`. Credentials, private backups, runtime databases, fixture server and local evidence are not committed.
