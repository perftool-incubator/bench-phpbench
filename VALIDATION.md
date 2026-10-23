# Validation record

Status: the adapter's full-profile and lifecycle acceptance was recorded on
2026-10-14. The updated Workshop recipe passed 22 focused tests and a
one-sample x86_64 smoke matrix across ten userenvs on 2026-10-23; that evidence
is at the end of this record. Earlier mechanics runs exposed a CDM aggregation
mismatch: its `avg` descriptor divided each sample's native score by the three
repeated-sample metric IDs. With `sum`, the corrected run's ordinary summary
preserved the native scores. The subsequent deadline, PHP configuration
provenance, signal-handoff, and archive-validation changes are covered by the
validation sections below.

## Source and runtime probes completed

- Zathras's `test19` selects `--sub_test phpbench`; the PTS profile is
  `pts/phpbench` 1.1.6, PHPBench 0.8.1, `-i 2000000`, and three PTS runs.
- The original 9,482-byte PHPBench 0.8.1 source archive from the Pure-FTPd
  mirror has MD5 `9ee2e4305e320c7f20a3d81978c8d0dc` and SHA-256
  `deff97dd1372405dbad3bf633009ca8446cb9770c5effa40cc45849a461c1b4a`.
  These identify the upstream tarball used by Workshop.
- The PTS `pts/phpbench` 1.1.6 `downloads.xml` instead specifies
  `phpbench-081-patched2.zip`, size 32,897 bytes, MD5
  `3e5fa1ad60261ff052bb38f505c8c4e5`, and SHA-256
  `32503bd4ace0c8429493de864ca48bb16febed867e52b75f4369d7145f797718`.
  Comparing that patched payload with upstream identified seven modified PHP
  files. The checked-in patch applies with zero fuzz and reproduces the PTS
  payload file-for-file. Patch SHA-256:
  `0fd949928a854134ac40561762c133f9c55edefd9c60f7a072cf680f3209d7e5`.
- In the isolated AlmaLinux 9.8 Crucible image with PHP CLI 8.0.30, probes from
  the intended install path exited 0 and emitted all 56 timed rows: 1,000
  iterations scored 527300; 2,000 iterations scored 539363; the profile-default
  2,000,000 iterations scored 543247 and completed in 37 seconds. Each run
  retained the PHP warning from `test_switch.php` on stderr.
- A probe installed under `/opt/phpbench` executed tests twice because that
  location matches one built-in PHPBench test search path as well as relative
  `tests`; it still reported only 56 tests. The Crucible install path avoids
  that collision, and output validation now checks the full ordered test-row
  list.
- Ten focused tests pass for parameter bounds, runtime estimates, the complete
  native output row list, summary checks, and post-processing validation.
- Rickshaw, Multiplex, Workshop, and benchmark-metadata documents validate
  against the checked-in Crucible schemas. Multiplex expansion of an empty
  parameter set produced the expected default `iterations=2000000` with role
  `client`; the runtime helper reports 37 seconds at that default and one
  second for 2,000 iterations.
- Source review removed the `PHPBENCH_INSTALL_ROOT` environment override. The
  root-running Workshop builder now uses the fixed benchmark install directory,
  so ambient environment variables cannot redirect its recursive replacement
  step. The ten focused unit tests and Python syntax checks pass after this
  change.
- A fresh root Crucible run built the `alma9` PHPBench requirement stage in 96
  seconds, deployed it to the remote client engine, ran three default samples,
  post-processed each, indexed the output, and generated an ordinary summary.
  Every sample completed 56 tests. The native scores were 975499, 974615, and
  957505; the first descriptor used `default-aggregation=avg`, so the summary
  values were each divided by three. The run file had CPU partitioning off and
  tools disabled; no tool data directory was produced. No shared CDM or
  Crucible change was made.
- A corrected full root Crucible run on the single selected remote x86_64 SUT
  completed three profile-default samples, post-processing, indexing, and
  ordinary summary generation.
  The summary retained native scores 954390, 971083, and 962781 (mean
  962751.333); the three measurements were 20.970, 20.609, and 20.786 seconds.
  CPU partitioning stayed off and tools stayed disabled. The same Workshop
  requirement image was reused. No other SUT was used, and no shared Crucible
  or CDM source was changed.
- Initial supported architecture is x86_64; aarch64 is not claimed before a
  native build/run there.

## Deadline, PHP configuration, and process lifecycle hardening

Before the fresh runtime acceptance below, the focused hardening changes had
been validated locally only. Current Rickshaw
sets the client-start roadblock to the runtime-helper estimate plus 180 seconds.
The adapter now budgets the full client from entry through preflight and result
validation at estimate plus 90 seconds, ending its native-work window at
estimate plus 75 seconds to reserve 15 seconds for cleanup. That leaves 75
seconds of roadblock margin after the cleanup reserve at every supported
iteration count:

| Iterations | Helper estimate | Client total deadline | Native-work cutoff | Rickshaw roadblock |
|---:|---:|---:|---:|---:|
| 50 | 1s | 91s | 76s | 181s |
| 2,000,000 | 37s | 127s | 112s | 217s |
| 10,000,000 | 185s | 275s | 260s | 365s |

The process wait budget is reduced by preflight time. Local tests exercise
these minimum, default, and maximum calculations. The maximum-iteration SUT
timeout and Rickshaw roadblock trial are recorded in the current status below.
The replaced `2 * estimate + 60` formula would have allowed 430 seconds at
maximum iterations against a 365-second Rickshaw roadblock.

Workshop and runtime remove `PHPRC` and `PHP_INI_SCAN_DIR` while leaving PHP's
normal default configuration loading active; they do not use `php -n`. The
format-2 build manifest records loaded/scanned INI paths and SHA-256 hashes,
effective directive values, PHP and Zend module lists, and PHP RPM NEVRAs. The
runtime re-captures and compares that information before launching PHPBench.
Local tests use synthetic PHP probe output and verify INI, module, package, and
environment tampering rejection. The fresh Workshop build and SUT run below
verify the recorded configuration in the deployed userenv; it has not been
matched against a live PTS execution.

SIGINT/SIGTERM received during process creation are queued until the new process
group is registered, and the client's finalizer cleans up the whole owned
process group on success, timeout, signal, and exceptions. Focused tests cover
a signal at the launch handoff, timeout output retention, generic-exception
cleanup, and descendants surviving a leader exit. These are local
helper-process checks, not a Crucible cancellation trial.

`python3 -m unittest discover -s tests -v` passed 18 local tests. Python syntax
checks passed for the client, builder, common helpers, postprocessor, and tests.
At that point, no SUT, Workshop image build, or Crucible run had used the
hardened code. Its format-2 manifest required a fresh Workshop build before
runtime acceptance.

## Archive-validation review follow-up

The Workshop source `unpack` command now invokes the PHPBench builder to check
the upstream tarball's size, MD5, and SHA-256 before parsing or extracting it.
The preflight rejects unsafe paths, duplicate members, links, special files,
special permission bits, and oversized contents. It writes only validated
regular files and directories to a temporary directory, checks the extracted
paths, file types, content hashes, sizes, and modes against the archive, and
then renames the result into place. The following build command repeats the
archive and unpacked-tree verification before patching.

The focused command `python3 -m unittest discover -s tests -p 'test_phpbench.py'`
passed 22 local tests, including checks that the Workshop recipe selects the
validated unpack hook and that an incorrect archive digest and a traversal
member never reach extraction. A synthetic valid archive also passed the
inventory and executable-mode checks. Python syntax checks passed for the
builder and test file. `workshop.json`, `rickshaw.json`, `multiplex.json`, and
`benchmark-metadata.json` validated against the current Workshop, Rickshaw,
Multiplex requirements, and Crucible metadata schemas, respectively. At that
point these checks had not exercised Workshop's real source stage or a rebuilt
userenv. The fresh build and SUT run below provide that runtime evidence.

## Adapter validation status (2026-10-14)

- The current source, including format-2 PHP configuration provenance and
  archive validation before extraction, passed 22 focused unit tests, Python
  syntax checks, and the current Workshop, Rickshaw, Multiplex, and benchmark
  metadata schema checks.
- A fresh `alma9` PHPBench requirement image was built for the hardened source.
  A subsequent root Crucible run completed all three profile-default samples,
  post-processing, CDM indexing, and ordinary summary generation. Each sample
  had a valid manifest, verified PHP configuration, and all 56 native rows.
  The native scores and summary scores matched: 966096, 963353, and 970918
  (mean 966789; standard deviation 3829.816314).
- Current-source Crucible lifecycle trials on x86_64 also passed: SIGTERM to
  the verified adapter produced an interrupted manifest and removed the owned
  process group; pausing the verified native PHP process group caused the
  adapter's 10,000,000-iteration deadline to time out and clean up. Both trials
  retained output, stderr, and error files and left no live adapter or native
  process. Since each trial deliberately aborts its only sample, Crucible
  exits nonzero and cannot produce an ordinary benchmark summary. See
  the separate development-workspace test records for run IDs and raw evidence.
- The timeout-abort run logged a Node `EADDRINUSE` error from a CDM indexing
  worker; indexing still reported completion. No Crucible or CDM source was
  changed as part of this PHPBench work.
- Initial supported architecture remains x86_64. AArch64 and controller-led
  cancellation have not been validated.
- The successful scores document run mechanics and repeatability on one SUT;
  they are not a hardware comparison or performance claim.

## Userenv compatibility smoke matrix (2026-10-23)

The updated default Workshop recipe was exercised through the PATH `crucible`
wrapper on the same test endpoint used for the bench-stream matrix.
Each x86_64 run used PHPBench's default 2,000,000 iterations, one sample, CPU
partitioning disabled, and no tools. All ten userenvs completed image
preparation, endpoint and engine setup, the PHPBench sample, and result
indexing:

| Userenv | Result |
|---|---|
| `alma10`, `alma9` | Pass |
| `fedora43`, `fedora44` | Pass |
| `rhubi10`, `rhubi9` | Pass |
| `stream10`, `stream9` | Pass |
| `alma8`, `rhubi8` | Pass after the EL8 package corrections below |

The first Alma 8 attempt built the image but failed the PHP runtime probe
because the EL8 `php-cli` package did not provide `json_encode()`. The RHUI 8
attempt stopped earlier because installing `coreutils` conflicted with the
base image's `coreutils-single`. The Workshop recipe now adds `php-json` only
for Alma 8 and RHUI 8, keeps the Python 3.12 selection for those userenvs, and
does not request a redundant `coreutils` package. Both corrected EL8 runs
passed the complete smoke path.
The successful retry run IDs were Alma 8 `3ac88a75-acc8-4475-aabb-eaa1febfd509`
and RHUI 8 `e141ac28-7903-4aa1-b1ff-288db2ea0d62`.

The focused command `python3 -m unittest discover -s tests -p 'test_phpbench.py'`
passed all 22 tests after these changes; JSON parsing and `git diff --check`
also passed. This is one-sample compatibility evidence on x86_64. It does not
repeat the three-sample profile-default acceptance run or establish performance
results on these userenvs.
