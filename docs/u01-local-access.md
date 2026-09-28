# U01 restricted local engineering access

`scripts/engineering_local_proxy.py` reuses the existing `/ui/engineering` assets
and Controller on Spark port 9000. It is a localhost proxy, not another business
API or bridge. Start it in the trusted local Python environment with Paramiko,
passing `--run`, `--ssh-host`, `--ssh-port`, `--ssh-user`, `--credentials`,
`--known-hosts`, `--state` and optionally `--port`. The credential file remains
private; no secret belongs in the command value, URL, HTML, browser storage or log.

Only one explicitly registered Run is exposed. Exact paths/methods, Host,
Origin/Fetch-Site, HttpOnly SameSite session, run owner and current version are
checked. The remote Controller remains authoritative for expiry, attempts,
approval and export. POSTs are never automatically replayed after an uncertain
connection failure. The original T27 approved Run is always read-only. The local
OS account and local processes are trusted; this is not multiuser remote login.

The page hides the old credential input and shows actual executor/reviewer model
identity, test summaries and review evidence. No mock success or live metric is
invented. Creation remains the verified Feishu `创建 材料检查` command; a new Run
must be explicitly registered by restarting the local proxy with its ID.

Real browser self-test on 2026-09-28 retained three new Runs: the first initial
candidate passed, but feedback after the effective 600-second invocation window
was denied by persistent budget; the second initial candidate passed after using
both Task attempts and remains pending founder approval; the third accepted a
real browser location comment and executed repair, but its independent review
failed exact evidence binding. It is a visible failure, not a successful revision
2. No budget was reset, no existing approval borrowed, no real approve submitted.
The final local entry registers the second pending candidate. Feedback is disabled
when the two-attempt cap is spent. Approval expires at the version's recorded time.

U01 interface readiness is distinct from founder acceptance and successful repair.
Live stale-version/export/path rejection checks and CPU boundary tests are recorded
in the T28–T29 evidence package. Product source/production deployment remain
5750270; this optional local tool does not require production release.
