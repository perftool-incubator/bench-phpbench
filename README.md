# bench-phpbench

This benchmark runs the PHPBench 0.8.1 source workload directly from a
[Crucible](https://github.com/perftool-incubator/crucible) client engine. PHP, the
upstream benchmark source, and its reviewed compatibility patch are prepared
through Workshop. The runtime does not install
or invoke Phoronix Test Suite.

The benchmark is client-only. Each native invocation runs all 56 PHPBench tests
and emits the upstream aggregate integer score; higher is better. The single
runtime parameter is `iterations`, which maps to PHPBench's native `-i`
argument. It defaults to 2,000,000 to match the Zathras-selected PTS profile.
For profile-equivalent repetition, set the Crucible run file's
`run-params.num-samples` to 3, matching PTS `TimesToRun=3`. Sample count is a
Crucible run setting, not a PHPBench parameter.

The PTS 1.1.6 profile calls this workload `pts/phpbench`, version 0.8.1. Its
`downloads.xml` pins `phpbench-081-patched2.zip` with MD5
`3e5fa1ad60261ff052bb38f505c8c4e5` and SHA-256
`32503bd4ace0c8429493de864ca48bb16febed867e52b75f4369d7145f797718`. This
project instead pins the original upstream Pure-FTPd tarball (MD5
`9ee2e4305e320c7f20a3d81978c8d0dc`; SHA-256
`deff97dd1372405dbad3bf633009ca8446cb9770c5effa40cc45849a461c1b4a`) and
applies a local patch whose resulting files exactly match the PTS archive. The
patch updates removed PHP behavior and changes several old test expressions; it
also disables some upstream regression assertions. Those changes are included
to preserve the workload Zathras selected and are recorded in the manifest. The
source license is preserved in `third-party/PHPBENCH-LICENSE.txt`.

PHPBench searches several fixed system locations plus the current directory
for tests. Installing under `/opt/phpbench` can make it discover the same test
files through both `/opt/phpbench/tests` and `tests`, running each test twice
while still printing `Tests: 56`. Workshop installs it under
`/opt/crucible/software/bench-phpbench/0.8.1`; the adapter checks all 56 timed
test rows to reject duplicate or missing work.

The PHP 8.0.30 Alma 9 probe emitted one compatibility warning from the old
`test_switch.php` source but completed all 56 tests and produced a score. The
client retains stderr and rejects failed processes, fatal PHP errors, missing
tests, duplicate tests, and output/manifest mismatches.

## Userenv compatibility

The Workshop default prepares PHPBench for the selected userenv. Alma 8 and
RHUI 8 use Python 3.12 for APIs missing from their system Python and install
`php-json`, which is separate from `php-cli` in those EL8 images. The recipe
does not explicitly install `coreutils`, avoiding a conflict with RHUI 8's
base `coreutils-single` package.

A one-sample x86_64 smoke run completed on `alma10`, `alma9`, `fedora43`,
`fedora44`, `rhubi10`, `rhubi9`, `stream10`, `stream9`, `alma8`, and `rhubi8`.
This verifies image preparation and benchmark execution on those userenvs; the
single sample is not equivalent to the three-sample PTS profile.

See [DESIGN.md](DESIGN.md), [VALIDATION.md](VALIDATION.md), and the shared
[benchmark integration lessons](docs/benchmark-integration-lessons.md).
