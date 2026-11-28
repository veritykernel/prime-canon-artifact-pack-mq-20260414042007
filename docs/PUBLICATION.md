# Rendered publication

Use `scripts/publish_rendered_subject.sh <subject-slug>` for the configured
stage destination. The target directory must already exist. The guard accepts
only this verified publisher or its no-effect dry run.

Set `PRIME_CANON_TRUSTED_PYTHON` to a separately qualified, absolute Python
interpreter outside the repository with the validator dependencies installed.
The guarded route rejects an unset interpreter, symlinks, unsafe ownership or
writable ancestors; it never sources the repository's `.venv`. The configured
stage directory and its ancestors must also pass owner and write-permission
checks. Publication remains held until these installed prerequisites and a
current render generation are verified.
The guard runs the external interpreter in isolated mode and loads trusted
dependencies before adding the frozen repository scripts after site packages,
so a checkout-local `yaml.py` cannot shadow the installed parser.

The publisher checks the current view, render execution receipt, and drift
receipt against the expected subject and output path. It reads the rendered
source once, independently derives the deterministic body from the governed
node and render-policy snapshot, verifies the receipt SHA-256, and
publishes those same bytes with an atomic replacement inside an open target
directory. It rejects source and destination symlinks and leaves the existing
target untouched if verification fails. A green drift receipt alone cannot
authorize later changed bytes.

The current view binds its complete spec, the corpus index, the exact bytes of
each active canon node, and the four render policy files into `view_hash`. Export prep and the
publisher recompute that hash on every use. `check_render_drift.py` is read-only:
it reports stale inputs without changing an immutable receipt or the view.

An existing subject with a legacy view hash must receive a reviewed successor
render generation before it can publish under this binding. Run
`python3 scripts/build_rendered_projection.py . <subject-slug> v2` in a
review branch. This adds `render-execution-v2.yaml` and `render-drift-v2.yaml`,
and the current view names that pair while preserving the original receipts.
Use a new generation label for each later render. The validator and CI history
check reject updates or removal of previously committed immutable receipts.
