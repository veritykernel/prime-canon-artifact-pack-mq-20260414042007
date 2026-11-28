# Protected-base guard qualification

The source repair in PR #4 reached protected `main` at
`a1f83284a498e8159784e8590ba340f9939c5be1`. Its tree
`15b76741c2587f81ee1aecc0fc11b8b8d25a2e2d` matches the independently
approved PR head `a5dfe47ec871a0ed64a4fc583eea71bd5bafe878`.

This documentation-only successor exercises `publish-guard-trusted` from
protected-base code against a PR candidate. Its exact-head hosted result must
be read back before that context is added to the required-check ruleset.
The installed rule must retain strict app-bound `validate` and
`publish-guard-check`, require a code-owner approval after the last push,
and keep zero bypass actors.

This qualification is source and branch-protection evidence only. It does not
authorize a signed promotion, local publication, merge-queue proof, Daybreak
finding disposition, or a paid rescan.
