# T07–T10 recovery and three-plane synchronization

The original repository remains `https://github.com/JimChen-g/cofounder-os`.
Use the existing development branch and normal protected pull-request checks; do
not rewrite history or force-push to establish matching versions.

## Public source versus private recovery state

Git stores source, dependency declarations, deployment instructions and tests.
Never commit runtime SQLite files, user identities, API keys, model weights or
unredacted logs. The isolated deployment root is outside the source checkout.

A complete recovery set contains:

- a verified Git bundle, source archive and commit/tree IDs;
- the exact dependency versions and model revision/weight hashes;
- the private mode-0600 service configuration and model API key;
- a consistent copy of the three SQLite ledgers and any Run/artifact state;
- the persisted paired identity, plus an integrity manifest;
- a restore verification using extracted copies, never the live databases.

The model weights are not duplicated: reuse the documented read-only cache and
verify all 14 SHA256 values against the locked revision. Acquire the same revision
from ModelScope if that cache is unavailable. A source copy alone is not a complete
runtime backup, and a running deployment is not an independent recovery snapshot.

## Spark snapshot procedure

1. Record current source hashes, deployed commit, process state and inbox facts.
2. Create a private snapshot directory outside `src` with mode 0700.
3. Briefly stop only this deployment's bridge and Product API using
   `scripts/manage_t07_t10.py stop`. Leave the model and unrelated jobs running.
4. With the writers quiet, use Python `sqlite3.Connection.backup()` for each
   runtime database. Check `PRAGMA integrity_check` on each copy. Copy other
   private state and configuration while the same quiescent boundary holds.
5. Package source, configuration/state copies and a SHA256 manifest. Do not put
   the live `-wal`/`-shm` files into the recovery databases. Set archives to 0600.
6. Restart API/bridge in a `finally` block, then verify authenticated API health,
   WSS connection, original message attempts and the existing other GPU jobs.
7. Extract into a separate private directory; compare every manifest hash,
   integrity-check all restored databases and verify the deployed commit.
   Do not start a second live bridge from the restore directory.

Keep snapshot and restored-test files private. Publish only file counts, hashes,
commit IDs, statuses and redacted paths. The actual paired-user identifiers and
configuration remain in the private snapshot.

## Mac recovery and deployment

Create a Git bundle with all local refs, run `git bundle verify`, and clone it into
an isolated restore directory. Verify restored HEAD/tree IDs and run the minimal
T07–T10 acceptance entrypoint against the restored source. This can reuse the task's
isolated dependencies; it is not a claim of reinstalling onto a new physical Mac.

Keep the Spark private recovery archive in a separate mode-0700 Mac directory,
never in public outputs. The source bundle plus private snapshot supports both
code and runtime recovery. Preserve the original independent Spark snapshot too.

Restore source under the configured runtime root; recreate a Python 3.12 venv and
install `.[bridge]` (or restore from the recorded offline ARM64 wheels). Restore
private config and SQLite snapshots into their original relative paths, retaining
0600 configuration permissions. Ensure no old API/bridge instance holds the lock;
then use the management script to start the services and perform authenticated
health checks. Persisted uncertain message states must not be silently reset.

A clean secondary checkout may be fast-forwarded normally after the authorized
merge. If it is dirty, diverged, or cannot be written, leave it intact and identify
the primary checkout explicitly. Never reset another workspace to make a report
look synchronized.

## Scope retained

This synchronization does not start T11 or later development. X02 remains the
original single-challenger experiment, at most 90 minutes, stopping by 2026-09-27
10:00 Asia/Shanghai. No Nemotron standby expansion or X02a–e scope is introduced.
The original Dynamo timebox and stopping line are unchanged.
