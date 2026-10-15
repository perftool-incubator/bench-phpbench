# bench-phpbench contributor instructions

- Read `docs/benchmark-integration-lessons.md`, `DESIGN.md`, and the current
  Crucible implementing-a-new-benchmark guide before editing this project.
- Preserve the native workload identity: upstream PHPBench 0.8.1 plus the exact
  compatibility patch represented by the PTS 1.1.6 `patched2` archive. Do not
  add Phoronix Test Suite as a runtime dependency or invoke its harness.
- Keep Workshop inputs separate from benchmark parameters. `iterations` maps to
  PHPBench `-i`; the three PTS repetitions map to Crucible run-file
  `num-samples=3`.
- This project is client-only. Do not add server hooks or CPU partitioning
  requirements.
- Preserve and validate all 56 native test rows. Never accept a sample based
  solely on the summary's `Tests: 56` field; duplicate source search paths can
  still execute tests twice while reporting that same count.
- Keep the install root outside PHPBench's hard-coded test search paths.
  Workshop provenance must continue to hash the source archive, compatibility
  patch, PHP CLI binary, and installed source tree.
- Update the shared benchmark integration lessons with verified findings and
  keep the latest copies synchronized.
- Do not commit, publish, push, or modify the Crucible checkout/catalog without
  the user's explicit instruction. Keep target names, credentials, and
  infrastructure-specific values out of publishable files.
