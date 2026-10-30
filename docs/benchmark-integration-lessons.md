# Benchmark integration lessons

This living document records practical lessons from adding benchmarks to
Crucible, beginning with CoreMark.
Use it alongside [Implementing a New Benchmark](implementing-a-new-benchmark.md).
The implementation guide defines the interfaces; these lessons explain what
to inspect, what can go wrong, and what evidence is needed before publication.
Check the current code and schemas when applying them to another benchmark.

Read this document before starting the next benchmark. Extend it when another
integration reveals an important detail: explain the failure or surprise, the
owning interface, and how the next agent can verify the behavior. Distinguish
observed behavior from hypotheses and pending validation. Correct or retire
lessons when the underlying code changes. Keep private infrastructure and
credentials out of examples, and link detailed interface specifications rather
than duplicating them here.

## 1. Establish the workload contract before writing the adapter

Read the source harness, its benchmark wrapper, any shared wrapper helpers,
and the native benchmark. Read comparable Crucible subprojects as well.
Record:

- The required client and server roles, including defaults when roles are
  omitted. A wrapper's name or packaging does not establish its execution model.
- What each parameter means, its default, and its valid range. An option named
  `iterations` may mean repeated benchmark executions in one wrapper and native
  work iterations in another.
- Whether parallelism means threads in one process, independent processes,
  multiple engines, or some combination of these.
- What makes a result valid: native validation, minimum measurement duration,
  units, and the meaning of the headline score.
- Which system configuration is required and which tuning is optional.

Many workloads migrated from Zathras may only need a client role. Confirm this
from the launch path instead of adding a placeholder server. In Rickshaw,
inspect both schema defaults and the code that applies them.

CoreMark's native score already combines the contexts within one process.
Its validation modes and minimum measurement duration must remain intact.
Those facts are more useful than copying a wrapper's command line unchanged.

OpenSSL showed that emitted metrics and native work duration can differ. The
pinned PTS profile invokes upstream `openssl speed`; its selected digest score
uses 256-byte input and its cipher score uses 8192-byte input. With OpenSSL
3.3.0, `speed rsa4096` runs ten timed phases: four legacy RSA operations,
three RSA KEM operations, and three generic signature operations. The selected
profile values are only legacy sign and verify, but the client still executes
and must validate all ten phases. Count native operations, not emitted metrics,
when estimating runtime. The profile's `TimesToRun=3` is harness repetition;
Crucible expresses it with run-file `num-samples=3`, not a benchmark parameter.

Combining related workloads into one subproject does not establish that they
use the same native implementation. Zathras's STREAM wrapper contains an OpenMP
STREAM source, while its NUMA STREAM wrapper launches AMD STREAM Dynamic and
installs jemalloc. Installing an allocator does not establish its use: this
wrapper omits the AMD build script's jemalloc flag, which defaults to disabled.
Inspect the actual compile/link command and preload environment before recording
the effective allocator. Inspect both launch paths before choosing a shared
adapter. Preserve material differences through explicit workload identity and
documented parameters rather than assuming NUMA placement is the only difference.

## 2. Keep build inputs and benchmark parameters separate

For database workloads, verify native connection selection as well as readiness.
HammerDB's MariaDB driver selects a Unix socket for a local host if its socket
setting is non-null, even when the host is `127.0.0.1`. A successful external
TCP login does not establish that this different authentication path works.
The reviewed adapter forces the native TCP path with the literal `null` socket
setting and validates the same generated account.

Workshop and userenvs supply the software environment. Multiplex and the
benchmark adapter select supported workload behavior at runtime.

| Concern | Where to implement and verify it |
|---|---|
| Packages, source acquisition, compiler and installed binaries | Workshop recipe and the selected userenv |
| A compile-time variant, such as a prepared thread count | Workshop build inputs and installed variant provenance |
| Runtime variant selection, native work count and deadline | Multiplex validation, adapter parsing and run manifest |
| Endpoint deployment and CPU partitioning | Endpoint settings and engine bootstrap |
| Reported metrics and aggregation | Postprocessor, CDM descriptors and query results |

Do not carry over a source harness's package assumptions. A list of DNF package
names is not evidence that each Crucible userenv can acquire those packages.
Use Workshop's supported source-build mechanisms where packages are unavailable.
Build and install during image preparation; the benchmark client should execute
the prepared software without fetching dependencies or compiling it.

PHPBench's 2026 x86_64 smoke matrix found two EL8 packaging issues: Alma 8's PHP
CLI needed `php-json` for `json_encode()`, and RHUI 8's base `coreutils-single`
conflicted with explicitly installing `coreutils`. Keep `php-json` in the
`alma8`/`rhubi8` Workshop override and avoid reinstalling `coreutils` there.
The Alma 8 and RHUI 8 base images already expose Python 3.12 as `python3`.
Requesting the DNF `python3` package selected `python36` and changed that
default. A 2026-10-30 PHPBench follow-up removed `python3` from
`phpbench_runtime_packages` and dropped `phpbench_python312_default`, including
its alternatives and pip installs. Both Stage 7 builds and one-sample x86_64
runs completed and indexed (Alma 8 `241b32b0-a85e-4a1f-9695-cec199d75e69`,
RHUI 8 `efc2de4f-af07-46b8-9284-b191a812d142`). The explicit Python override
is unnecessary on these tested images; the `php-json` exception still applies.
Keep checking package resolution and a real sample on each target because an
image build alone does not prove runtime compatibility. These smoke results do
not qualify performance.

Workshop builders often run as root. Keep destructive staging/install paths
fixed to the benchmark's intended directory; do not let an ambient environment
variable redirect recursive replacement or cleanup. Keep host identities in
local run files and records, not in files intended for publication.

An isolated build in an AlmaLinux 9.8 image with OpenSSL's Perl build modules
already present verified the pinned source builder and `make install_sw`, but it
did not exercise Workshop/SIS package resolution. Keep that evidence separate
from the userenv CI that proves DNF can provide every declared package.

The later full Crucible run built OpenSSL's Workshop requirements stage inside
the `alma9` userenv, resolving the declared DNF packages and installing the
pinned source successfully on x86_64. That confirms the stage for that tested
architecture; the same userenv advertises AArch64, which still needs its own
build/run CI evidence.

For source builds, pin a revision and retain compiler flags, source integrity
checks, binary hashes and build logs. Include the actual port headers and build
files among relevant integrity inputs. CoreMark's pinned upstream integrity
check had a stale header digest: record such a failure honestly and explain any
independent verification rather than claiming the upstream check passed.

Check actual binary storage before rejecting prepared variants as too large.
During STREAM design review, two OpenMP builds with array lengths 1,048,576 and
67,108,864 produced identical 22,160-byte executable sizes: their different array
allocations were in a `NOBITS` BSS section. This does not reduce runtime memory
needs. Evaluate image size, build cost and runtime allocation separately, and
keep the native workload identity intact when choosing a variant strategy.
Compile each declared size: in the reviewed x86_64 GCC build, a 1 GiB-per-array
variant failed to link under the default code model, while a 2 GiB-per-array
variant linked with `-mcmodel=medium`. Keep such target-specific build flags in
Workshop provenance and verify other architectures independently.

## 3. Verify defaults through the real tools

Keep dependency checks separate when software generates executable scripts.
HammerDB v6 `loadscript` successfully generated database workloads on a host
where explicit `package require mariatcl` and `package require Pgtcl` failed
because shared client libraries were absent. Startup and `ldd` of the CLI did
not establish those dependencies. Record script generation, actual package
loading, database connectivity and native execution as distinct checks. The
first Workshop-built Alma9 trial subsequently passed both database workloads;
this does not qualify other userenvs or architectures.

In CoreMark's first adapter, Multiplex `essentials` replaced explicit requested
values. A request for one thread expanded into two thread variants. A JSON
schema check and parser tests did not catch that behavior.

Exercise the installed Multiplex CLI with:

- An empty parameter set.
- Each explicitly supported variant.
- A partial parameter set that relies on runtime defaults.
- Explicit nondefault values for every configurable parameter.
- Invalid and out-of-range values.

Compare the expansion, launched command, selected binary and effective manifest.
Keep fallback defaults consistent where the adapter supplies omitted arguments.
Bound native work counts as well as wall-clock deadlines.

Inspect `presets.defaults` as well as `essentials`; fio demonstrates their
different purposes. In the Multiplex implementation inspected for STREAM,
`override_presets()` applies `defaults` only to an entirely empty parameter set,
not to each missing field of a partial set. Start-script defaults therefore
matter when a user supplies only some parameters. Match the intended defaults
in both interfaces, and verify partial requests through the CLI and adapter.
Do not use `essentials` to fill ordinary optional values when that would
replace an explicit user selection.

Endpoint settings and benchmark parameters are different interfaces. For
example, disabling tool execution did not prevent default tool image selection
in the initial CoreMark run. An explicit empty `tool-params` list selected the
intended benchmark-only image path. Inspect the actual image request too.

## 4. Test the declared executable entry points

A service UID must traverse every storage ancestor. Rickshaw's engine sample
parent is created with `mkdtemp()` and mode 0700; making a benchmark child
directory traversable cannot grant `mysql` or `postgres` access through that
parent. Check permissions under the actual service UID. Use adapter-owned
traversable parents or explicit writable mounts, keep database leaves private,
and leave framework directories and caller-owned mount roots unchanged.
HammerDB's mechanics default uses an owned `/tmp` root and records its backing
filesystem; this does not establish parity with a dedicated storage device.
Its `log-dir` places native redo/WAL, while diagnostics are archived separately.
Verify native variables, symlinks and files before shutdown, then owned-tree
removal and absence of live processes afterward.

A mounted sequential MariaDB repeat exposed a TCP-port reuse edge case. The
first sample succeeded and removed its database daemon, process group and
owned data/log children; the next sample rejected engine port 40000 before
starting a daemon. A target-side live probe found no owned process or directory
left from the first sample, but `/proc/net/tcp` showed that port only in state
`06` (`TIME_WAIT`). A bind/listen probe with `SO_REUSEADDR=0` returned
`EADDRINUSE`; enabling reuse allowed the completed port to be bound again.
For engine-local listener checks, enable `SO_REUSEADDR` and perform both
`bind` and `listen`: bind alone can share a port with another reuse-enabled
socket, while `listen` still rejects a live overlapping listener. Cover
listeners bound to both `127.0.0.1` and `0.0.0.0`, with reuse enabled and
disabled. Keep daemon startup as the final owner of the port and fail closed if
another process binds between preflight and startup; never fall back to
connecting to an existing database. Local regression tests exercised a real
loopback `TIME_WAIT` state and all four listener combinations. The mounted
repeat subsequently passed all eight samples with the corrected adapter,
including both MariaDB repeats. Independent review matched all native/API
values and verified owned process/storage cleanup.

Rickshaw invokes its configured hooks directly. A postprocessor that runs with
`python3 script.py` can still fail in Crucible if its Git mode is not executable.
The first CoreMark run found exactly that defect.

Resolve each path from `rickshaw.json` and verify its shebang, Git executable
mode, required environment and working-directory assumptions. Exercise the
declared entry point itself, including runtime and postprocessing hooks.
Retain the failure artifacts so a corrected postprocessor can be evaluated
without repeating an expensive workload unnecessarily.

## 5. Treat native output as the authority

Build fixtures from the pinned native implementation and then captured output.
HammerDB schema operations catch some errors internally; exit status zero and
an adapter completion sentinel are insufficient. Require native build/check
success, successful schema VU status and the expected SQL warehouse count.
Its serial schema build has one VU, while the timed run adds a monitor. Native
schema labels differ by backend and worker messages include `Vuser 1:`.
Invented fixture wording passed unit tests but rejected real native forms;
source review corrected this before the first database trial, whose captured
output confirmed the parser. Postprocessing must revalidate retained setup
logs as well as timed output, iteration arguments and engine identity.

Keep the raw output, effective parameters, process status, timing and build
provenance together. Require native correctness checks before emitting a valid
performance metric. A zero process exit status alone may not establish native
benchmark validity.

OpenSSL 3.3.0 `speed rsa4096 -multi N` emits separate per-worker records for
legacy RSA (`F2`), KEM (`F9`) and generic signature (`F10`) operations, followed
by three aggregate summaries. Validate every worker's record in each group and
reconcile its rates with the corresponding summary. Checking only the selected
legacy sign/verify row can miss incomplete native work, even though other timed
operations contribute to the runtime.

Bind postprocessing to the iteration arguments as well as the retained output
and manifest. STREAM review found that a hook ignored iteration arguments and
could accept a valid Triad artifact for a Copy request. Internal
agreement between two files cannot detect a valid artifact from the wrong
iteration. Compare effective parameters, including start-script fallbacks,
before publishing a score.

For CoreMark this means checking both standard seed modes, expected CRCs and
context counts, iteration/score consistency, and the native minimum duration.
Only the performance mode supplies its throughput metric.

Record calibration and validation separately from measured work. When native
output provides duration but no absolute timestamps, document any reconstructed
measurement endpoints as estimates, explain the anchoring method and its
uncertainty, and check that they fit within the recorded process bounds.

If a process envelope is represented by nanoseconds but the metric interface
requires integer milliseconds, round its start down and completion up. Flooring
both endpoints can trim up to a millisecond from the completion bound. State
this quantization explicitly; an outward-rounded process envelope still does
not identify the native kernel's measured interval.

Test invalid output, manifest/raw-output disagreement, timeouts and interrupted
execution. A failed run must not produce a valid-looking performance score.
Clean up owned processes, including child process groups, on failure and abort.
Distinguish an adapter signal test from a full Crucible abort test.

Check process-group members even after the directly owned leader exits. A
zombie leader can make `killpg(group, 0)` appear live; reap it while checking
actual member state. Conversely, a reaped leader can leave live descendants.
Gate deletion of owned database and transaction-log files on confirmed group
shutdown, and keep deadlines active after stdout EOF. Owned helper tests
exercised zombie reaping, exited-leader descendants and stdout EOF supervision
during HammerDB review. Dedicated MariaDB and PostgreSQL adapter SIGTERM
trials inside Crucible subsequently verified cleanup and client-start-end
roadblock failure propagation. Both samples were invalid, with no completed
measurement timestamps, score, or indexed metric/measurement-period records.
Direct controller SIGINT or immediate cancellation remains untested.

A native counter-window start marker and running-worker logs are diagnostic
evidence for an interrupted sample, not a completed measurement. CLI process
age at signal receipt is not measured workload duration. Failed-run parameter,
tag and run metadata may still be indexed while metric queries return structured
`RUN_NOT_FOUND`; verify absence of accepted metric and measurement-period
records rather than claiming the entire run is absent from the index.

## 6. Define CDM aggregation before debugging CDM

Validate generated metric descriptors against stock CDM `indexDefs`, including
`metric_desc.names`. `CDMMetrics` accepts a names dictionary, but arbitrary keys
are not necessarily indexable: STREAM completed 16 native samples and document
generation before indexing rejected its unknown `result-variant` field. The
supported `desc` name can carry the `STREAM-derived` annotation. Require
successful indexing and ordinary period-scoped queries before claiming
integration acceptance. Valid native samples can be reprocessed for a
postprocessor-only correction; record the original native adapter and revised
postprocessor identities separately.

CoreMark initially declared throughput with `default-aggregation=avg`. That
decision led us to patch CDM before adequately comparing existing benchmark
contracts. The correct default for total throughput across independent clients
is `sum`, as used for fio and trafficgen throughput.

There are separate aggregation questions:

| Question | Example |
|---|---|
| What does the native process report? | CoreMark already totals its own threads; do not multiply the score by thread count again |
| How do concurrent metric series combine? | Independent clients producing 10 and 30 iterations/sec contribute total throughput 40 |
| How does a series summarize time? | A rate is weighted over its measurement interval; `sum` across clients does not sum every time sample into a rate |
| How do repeated benchmark samples combine? | The result summary separately calculates sample means and standard deviations |

Choose the default aggregation for the metric's meaning across breakout
dimensions. Do not choose `avg` merely because the native metric is a rate or
because repeated benchmark results should be averaged. Inspect existing metrics
with matching semantics, not only matching units or output shape.

PHPBench's score is already aggregated by the native program for each sample.
With three samples, a Crucible descriptor using `default-aggregation=avg`
caused each sample's score to be divided by three in the ordinary CDM result
summary. Changing the descriptor to `sum` preserved each native score; the
summary then calculated the mean across the three sample results separately.
For a new metric descriptor, verify both the per-sample result and the normal
multi-sample CDM summary before accepting the aggregation choice.

Inspect the generated period bounds before predicting a concurrent-client
summary. In the Rickshaw revision inspected for STREAM, same-name client
periods are combined using their intersection, not their union. Derive the
expected value from the actual query window and stock CDM weighting, including
its inclusive millisecond bounds and treatment of partially overlapping data.
Source inspection and synthetic endpoint-weighting checks do not establish
live concurrency acceptance. Summing native best-rate summaries also does not
establish instantaneous combined bandwidth when the kernels execute at
different times within their process envelopes.

Check single-sample summary semantics too. The inspected stock summary uses
sample standard deviation, which is undefined for one observation and appears
as JSON `null`. Do not replace that with zero in an independent acceptance
calculation; compare mean/min/max and require the expected undefined dispersion.

The investigation also exposed a genuine CDM defect: period-scoped `avg`
queries can count descriptors belonging to other samples in their denominator.
It is tracked separately in
[CommonDataModel #215](https://github.com/perftool-incubator/CommonDataModel/issues/215).
Original, unpatched CDM code returned all four native CoreMark scores correctly
with `sum`. A shared defect and an adapter contract error can coexist.

Before changing shared framework code:

1. Inspect the adapter's emitted descriptors, labels, IDs and intervals.
2. Compare comparable existing benchmarks and the documented metric contract.
3. Reproduce the calculation using the original framework revision.
4. Separate a benchmark-specific correction from a generic framework issue.
5. Validate multiple sequential samples and concurrent clients. A single client
   and single sample cannot reveal every aggregation error.

Independent review should challenge the metric contract as well as verify the
arithmetic. A patch that reproduces an expected number is not sufficient evidence
that changing the shared component was necessary.

## 7. Make CPU partitioning an explicit support decision

CPU partitioning is optional for CoreMark's initial integration. The accepted
development run used it disabled; target TuneD changes and a reboot were not
needed for that scope.

If a future benchmark requires partitioning, inspect actual engine bootstrap,
kernel CPU isolation and the effective cgroup affinity. Rickshaw can pin its
engine to housekeeping CPUs while supplying a separate workload CPU list.
Intersecting the workload list with an inherited housekeeping-only mask can
incorrectly produce an empty set. A mocked affinity test is useful but does not
establish live partitioned support.

Do not enable partitioning or turn it into a readiness requirement by default.
When it is required, prepare and verify system tuning before testing it; an
endpoint option alone does not create isolated CPUs.

NUMA topology discovery and workload placement are separate interfaces.
Uperf's `cpu-pin=numa` locates the NIC's NUMA node and applies CPU affinity;
it does not bind memory. Toolbox's sysfs topology helpers describe CPUs and
nodes, but do not establish what a container is permitted to use. Verify
effective CPU affinity and allowed memory nodes before selecting CPU and memory
policies. Inspect allocation and first-touch behavior in the actual native
workload before describing a placement mode as local or remote memory.

Check inherited runtime environment variables as well as the process mask.
During STREAM review, a small GNU OpenMP helper showed that inherited
`OMP_PLACES={1}` caused two threads to use CPU 1 despite `GOMP_CPU_AFFINITY=0,1`;
clearing `OMP_PLACES` restored the requested split across CPUs 0 and 1.
Normalize or explicitly account for conflicting OpenMP placement controls, and
verify effective worker placement rather than inferring it from the launch
command alone. This helper check does not establish live STREAM placement.

Verify each topology helper's return contract. Toolbox's
`get_thread_siblings(cpu)` excludes the requested CPU itself. STREAM's first
grouping attempt lost SMT relationships and could select duplicate CPUs on
wider SMT systems. Form the group from the CPU plus its siblings, intersect
with permitted CPUs, and assert that the selected CPU list is unique. Use a
fixture matching the actual helper rather than an assumed inclusive API.

Memory availability also depends on cgroup hierarchy. A leaf with an unlimited
`memory.max` can still be constrained by a finite parent. Walk visible cgroup
ancestors and account for their current usage and applicable limits, along
with host availability and explicit node placement. Fixture validation of a
finite parent limit is separate from a live constrained-container check.

For restricted allowed memory nodes, host-wide `MemAvailable` can include
memory the workload cannot allocate. STREAM review reproduced this gap with
ample global memory and insufficient free memory on the permitted node.
Account for observable permitted-node capacity, and fail clearly if the
topology needed for that check is incomplete. This remains a snapshot guard;
inherited memory policies and concurrent allocations need separate evidence.

## 8. Validate a development installation before publication

Use a local benchmark repository and development symlink first; add an
unofficial registration only if the test requires it. Keep it separate from
unrelated development checkouts. Crucible runs as root; verify that the
controller's container can resolve any symlink to the local repository. Reuse
approved registry configuration and the user's existing SSH identity. Keep
credentials and private infrastructure details out of published artifacts.

Record the framework revisions, benchmark revision or staged tree, run file,
expanded parameters, image identity, raw logs and queries used for acceptance.
A staged tree identifies contents and modes but is not a published commit.

Verify that every Workshop file input survives source-image-service bundling.
STREAM's first full trial failed before native execution because its required
`NOTICE.md` existed in the local checkout but was omitted from the image
request. The inspected client and service exclude `.md` files, along with
documentation directories. Keep build-required notices in an included format
and location, and check the materialized bundle as well as the local recipe.
A direct cached-image build with local mounts does not exercise this transfer.

A useful progression is:

1. Local source build, native validation and adapter checks.
2. Installed schema validation and benchmark discovery.
3. Workshop requirements-stage build and complete engine execution.
4. Multiple samples and prepared variants, followed by native/CDM comparison.
5. Default indexed queries and the ordinary Crucible result summary.
6. Failure/abort handling and the intended userenv/architecture matrix.

State which checks actually ran. A fresh benchmark requirements stage on a
cached base does not prove cold acquisition of all userenv dependencies.
Parser fixtures do not replace native execution. One tested userenv does not
establish support for every userenv named in a future CI plan.

Verify CI selection in the generator and reusable workflow, not just the
schema or scenario. At the Rickshaw revision inspected for STREAM, the schema
permits a per-benchmark userenv allowlist, but the job generator does not apply
it. The reusable workflow selects a global userenv matrix, and run-file
creation overwrites an explicit scenario userenv. Its Rickshaw checkout also
uses `master`; a benchmark development branch alone cannot supply a new CI
benchmark enum and scenario. Coordinate those dependencies or use an explicit
development workflow to test them. Treat unqualified matrix lanes as coverage
work to complete, and back support claims with actual builds and runs.

Check the first failing lifecycle phase before editing code. For example,
CoreMark execution passed while a missing executable bit broke postprocessing;
later, disk pressure blocked OpenSearch writes. An index command can report
success without proving the data is queryable. Query the run and compare its
values independently instead of trusting the command's exit status alone.

An earlier OpenSSL pre-publication test used only a local symlink under
`subprojects/benchmarks`; it did not need an official `repos.json` entry. A
fresh test of the hardened source then rebuilt OpenSSL 3.3.0 in the x86_64
`alma9` Workshop stage and ran three client-only SHA256 samples through
postprocessing, CDM indexing, and the ordinary summary (run
`172cf2d0-6a28-4a2e-b664-48065b47b34b`). Three current-source RSA4096 samples
then completed with both sign and verify metrics (run
`eeca95c1-9967-4238-84cd-41d558130dd1`). A 15-test Multiplex run completed
three samples each for SHA512, AES-128-GCM, AES-256-GCM, ChaCha20, and
ChaCha20-Poly1305 (run `0dba8111-7e4c-47c2-8903-0f7dbeba603e`). The retained
manifests confirmed the pinned archive/tree, absent `openssl.cnf`, active
default provider, sanitized `LD_*`/`OPENSSL_*` environment, successful exits,
and empty process groups. All seven declared algorithms now have current
x86_64 smoke coverage; this is not performance acceptance. Later x86_64
lifecycle runs covered adapter SIGTERM and native-work timeout cleanup for live
owned groups. Direct controller cancellation and AArch64 remain untested.

The RSA run's CDM indexing emitted a Node `EADDRINUSE` error from a `sync-rpc`
worker, then reported all documents present and completed. A direct CDM query
retrieved the first sample's secondary `rsa4096-verify-per-sec` value. When a
CDM helper logs an error but continues, query a non-primary metric directly
before treating indexing as complete; record the error separately instead of
assuming either data loss or harmlessness. No Crucible or CDM source change was
made for this benchmark.

Crucible also emitted Git errors because the local symlink target had no commit.
The tests prove the staged tree, not a published revision; after review, create
a commit and repeat checkout/provenance validation before publication.

Compare the expected iteration/sample matrix with the retained harness status
and accepted indexed results. In HammerDB's mounted baseline trial, two repeated
MariaDB samples failed while six native samples succeeded. The controller exited
zero and the ordinary summary reported `partial=false`, but omitted both failed
MariaDB iterations, including their successful first samples. The PostgreSQL
metric/period records alone were indexed, and only its iterations were
summarized. A clean exit or a nonpartial
summary therefore did not establish that the requested matrix passed. Retain
failed-attempt diagnostics and check missing iterations explicitly.

Preserve data and the development environment while investigating. Do not
silently relax disk thresholds or remove unrelated results and images. When
using an orchestration service, explicitly state the intended harness, the
development installation, and any no-update/no-teardown requirements. Confirm
that benchmark discovery recognizes the unpublished subproject.

## 9. Publish and integrate in dependency order

The benchmark repository must exist at its advertised URL and branch before
the official Crucible catalog refers to it.

1. Review the benchmark source, license/third-party notices, executable modes,
   example run file, metadata, validation evidence and stated support limits.
2. Publish `bench-<name>` under the agreed organization and verify a fresh clone
   of the advertised branch. Keep machine-specific test evidence local.
3. Add the official entry to Crucible's `config/repos.json` through a feature
   branch and PR. Validate it against `schema/repos.json` and confirm discovery.
4. Coordinate the real Rickshaw CI scenario, benchmark reusable workflow,
   intended userenv coverage and examples. An inactive workflow example is a
   template, not evidence that organizational CI passed.
5. Verify installation through the published clone source, rather than only a
   development symlink. Keep remaining CI or matrix work explicit in the PR.

For this migration effort, use the agreed implementation/review roles:
`gpt-6-luna` implements and `gpt-6.1-sol` reviews. Give reviewers the exact source
revision, test commands, raw evidence and unexecuted cases. Keep unrelated shared
framework issues in separate issues or PRs so a new benchmark's dependencies
remain clear.

## 10. Inspect suite configuration and profile semantics

The following Phoronix findings come from pinned native source inspection,
not successful engine execution. Runtime acceptance must verify the effective
configuration and behavior.

### Pin the suite, profile and acquired software separately

PTS profiles are versioned independently from PTS and their workload archives.
The inspected PTS can substitute a newer installed minor profile when the exact
requested one is absent. Require the exact prepared profile in preflight and
validate its retained XML Identifier after execution. Profile options, output
units and validity rules come from the pinned native profile/parser, not from a
wrapper's terminal-output reducer. The inspected `pts/openssl-3.3.0` profile returns separate sign/s and verify/s
results for RSA4096, and byte/s for its other selected algorithms. The wrapper
lost a duplicate RSA entry. The inspected `pts/sqlite-2.2.0` profile reports seconds
with lower-is-better direction despite the wrapper's throughput description.

### Verify effective configuration under the actual execution UID

PTS v10.8.1 running with writable /etc and /var/lib chooses system config and
download/scratch paths. HOME and a private PTS user-path override alone do not isolate its
settings. Resolve the actual config selection before relying on offline, upload,
telemetry or batch controls. Keep any isolation patch narrow, opt-in and pinned,
retain the applicable license and record the effective config and patch hashes.
Actual engine execution must verify those settings before runtime acceptance.

### Noninteractive defaults can expand or change the work

In the inspected PTS manager, batch all-combinations bypasses preset option
selection. Dynamic repetition can also extend work. Disable those behaviors
explicitly for a fixed selected combination and repeat count, and validate the
actual result cardinality and options. A saved result XML file does not prove
success: failed runs can retain entries with empty values and raw samples.
Require expected identifiers, units, direction, complete finite raw values and
summaries, and native failure checks. PTS already expands OpenSSL's decimal k
suffix when producing numeric results; do not multiply normalized XML again.

### Client-only does not imply service or port isolation

A native single-host service profile can daemonize, bind default ports and only
request shutdown without waiting. Inspect native pre/interim/post scripts and
generated run scripts. Account for host networking, per-engine port ownership,
resource bounds, readiness, daemon descendants and verified shutdown before
removing data. A native stop script and direct-child exit alone do not establish
cleanup. Adaptations must be explicit in provenance and workload descriptions.

### Inspect control precedence and failure cleanup

Pinned PTS v10.8.1 checks `NoInternetCommunication` before
`NoNetworkCommunication` in an `elseif` chain. Setting both true does not
activate the stronger branch. The reviewed OpenSSL adapter sets the first false
and the second true. Check effective native precedence rather than interpreting
boolean option names independently; actual network behavior remains a runtime
acceptance check.

Compare native summaries using precision derived from native repetitions and
the pinned calculation rules. Inferring tolerance from the supplied summary
text lets a coarsely rounded or corrupted value widen its own acceptance range.
Retain raw values without unnecessary float conversion while checking the
native rounding behavior.

A handled signal must not prevent the failure cleanup it triggered. Separate
normal interruption checks from bounded cleanup checks: preserve the recorded
signal, stop owned groups, retain diagnostics, and remove owned state only after
confirmed shutdown. A shared absolute deadline and a cleanup reserve must also
cover logging and file removal after process termination. Require targeted
runtime interruption trials in addition to source review. The native OpenSSL
Crucible SIGTERM trial (`c1c7161d-34b9-4956-818f-5a556548924a`) signaled only
the verified adapter while three owned SHA256 processes were active. The client
retained its output/error files, recorded signal 15 and an invalid interrupted
manifest, stopped the process group within 0.92 seconds, and needed no KILL
escalation. The native timeout trial
(`8bcf091a-e990-45a9-94dd-12418394a206`) paused only the verified OpenSSL
process group. At the 95-second work deadline, the adapter sent TERM and then
KILL to the stopped group, retained diagnostics, and recorded no survivors.
Both samples were rejected; during testing, `crucible get result` returned
`RUN_NOT_FOUND`. The controller/CDM output was not retained with the lifecycle
evidence, so those framework observations are not independently auditable from
the saved evidence files. These runs cover adapter SIGTERM and native-work
timeout escalation through Crucible. Direct controller cancellation remains
untested.

For future lifecycle runs, retain controller exit status and framework/CDM
query responses alongside the interruption probes. Adapter manifests establish
local rejection and process cleanup; independently reviewable framework
rejection and metric-absence claims also require retained controller output
and direct metric/measurement-period queries.

### Separate preparation evidence from workload acceptance

The initial OpenSSL Alma9/x86_64 Workshop build succeeded using the existing
controller source-images service. Earlier framework stages were cached; the
benchmark requirement stage was freshly built. Save both the outer build output
and the prepared image's native install log, preparation log and manifest. The
outer output may show only the native installer command and a final manifest;
it does not expose every compiler warning or installer diagnostic. Exporting
those files with a read-only, network-disabled inspection container can verify
the published image contents without executing the benchmark.

The first build confirmed acquisition, explicit package availability, native
PTS installation of the exact OpenSSL profile and a compiled prepared payload.
It did not establish runtime option selection, affinity, private effective
configuration, native XML acceptance, interruption cleanup or CDM integration.
Source-derived synthetic tests also remain separate from captured native
evidence. Record exactly which layer passed and retain the remaining gates.

### Scope live process observations to the owned engine

The inspected remotehosts Podman path uses the host PID namespace. In this
environment, both Podman 4 `top` and `podman exec ... ps` exposed host processes;
their output is not scoped to one benchmark container. Do not assume every
listed process belongs to that benchmark or treat missing HPID values as
evidence of no work. Verify the namespace and process identity, filter by the
exact container's cgroup, and record process start times to distinguish PID
reuse. This matters especially before targeting an adapter for an interruption
trial. Keep command-line capture restricted to the owned workload; unrelated
framework commands can contain credentials.

### Match the primary metric source to the benchmark name

The first native OpenSSL trials completed valid work, postprocessing and CDM
indexing, but ordinary Crucible summary generation failed: the benchmark was
`phoronix` while the adapter emitted only source `phoronix-openssl`. The inspected
Rickshaw generator identifies a primary metric only when its type matches and
its source equals the benchmark name; its primary reference is
`benchmark::metric-type`. This source contract is also documented in the main
implementation guide. A metadata declaration of the emitted source/type pairs
does not override that runtime contract.

Use the benchmark name for the primary source. Preserve different native
operations and units with distinct metric types, and use separate companion
sources only where the actual summary/query path supports them. Test emitted
descriptors and declared metadata together, then exercise ordinary summaries in
addition to native parsing and direct metric queries. An indexed value alone
does not prove end-to-end benchmark integration. Preserve original evidence
before correcting and replaying postprocessing, and distinguish the native
runtime source revision from the corrected postprocessor revision.

### Disable tool provisioning explicitly for mechanics trials

In Rickshaw `a01579b12f72678d69074a13189cecb3aa83eefb`, an explicit
`tool-params: []` replaces the default tool parameter list, but remotehosts
separately defaults `disable-tools` to false. To request a mechanics run without
collection tool engines, keep the empty parameter list and set
`settings.disable-tools: true` at the endpoint or per-remote scope. The
remotehosts deployment code honors that setting when building its tool-engine
list. Inspect the generated run file as well as its input template, because CI
creators can replace endpoint fields. Source references: Rickshaw
`schema/run-file.json` tool-params, `schema/remotehosts.json` disable-tools and
`endpoints/remotehosts/remotehosts.py` endpoint defaults and tool-engine creation.
The reviewed Phoronix native run files use both controls; the inactive CI
proposal was corrected to carry them too.

### Derive timing from every native operation the selected command runs

OpenSSL 3.3.0 `speed -seconds 30 rsa4096` performs more work than the two
sign/verify results selected by the pinned PTS parser. Native logs and source
show ten timed phases: four legacy RSA operations plus generic KEM
keygen/encaps/decaps and generic signature keygen/sign/verify. The initial observed
launch spacing was about 305 seconds, including possible PTS pauses; this is
not an exact native execution duration. Three PTS repetitions therefore require about
900 seconds of timed work, plus key-generation overshoot and PTS overhead; an
earlier estimate based only on four operations was incomplete. The configured
3600-second client budget accommodates that behavior without changing native
work. The exact `rsa  4096 bits` parser hint still selects only the intended
legacy sign/verify pair. Count all executed operations when estimating time,
and separately identify which outputs the profile publishes. Preserve native
repetition semantics when an estimate is wrong. Completed two-host, two-sample RSA acceptance confirmed native JSON durations
of 300.88–305.37 seconds per execution, with result identity, metrics and
cleanup reviewed separately from the timing estimate.

### Keep documentation searches from launching benchmark commands

An unquoted Markdown search pattern caused shell command substitution and
accidentally started the controller's system OpenSSL speed command during this
port. It was terminated as an owned process, then independently confirmed absent
from the controller; its output was excluded from acceptance evidence. Treat
shell command text as code even for read-only searches. Use literal
single-quoted search patterns or structured subprocess argument arrays when
text contains backticks or command substitution syntax. If this happens,
identify the exact owned process before stopping it, verify cleanup, and keep
incidental controller activity separate from the intended remote benchmark.

## 11. Port suite payloads as native workloads

When a harness profile supplies a patched workload archive, compare it with the
upstream release instead of assuming the profile points to pristine source.
Pin the upstream archive and the profile payload separately, review the file
diff, and represent required workload changes as a checked-in Workshop patch.
Record both hashes in build/run provenance. This keeps the native project
independent of the suite runner while explaining any differences from upstream.

PHPBench 0.8.1 exposed two concrete traps. Its source searches fixed test paths
including `/opt/phpbench/tests` and also searches relative `tests`; installing
under `/opt/phpbench` caused every test to run twice while the summary still
reported 56 tests. Install outside those fixed paths and validate every timed
test row, including uniqueness and completeness, instead of trusting the summary
count. Also keep the profile's `TimesToRun=3` separate from PHPBench's native
`-i 2000000`: the former maps to Crucible `num-samples`, while the latter is a
runtime benchmark parameter.

The captured PHPBench full-profile probe reported a 37-second default run. Its
runtime helper returns this observed estimate without adding safety padding;
Rickshaw adds its own roadblock padding. Keep the measurement-based estimate and
framework padding distinct. The patched workload also prints a nonfatal PHP
warning from `test_switch.php`; retain stderr and reject fatal/parse errors or
incomplete native results rather than treating all warnings as success or
failure.

### Bound PHP clients and pin their effective runtime configuration

Rickshaw's current client-start roadblock is the runtime-helper estimate plus
180 seconds. A benchmark client's own deadline must cover its preflight and
result validation as well as native execution, and must leave time for process
cleanup and diagnostics before the framework roadblock. In this PHPBench port,
the whole-client deadline is estimate plus 90 seconds, native work stops at
estimate plus 75 seconds, and 15 seconds are reserved for cleanup. The native
wait budget is reduced by elapsed preflight time. This keeps the full
`iterations=50` through `iterations=10000000` range inside the roadblock.
The previous `2 * estimate + 60` client timeout reached 430 seconds at the
10,000,000-iteration maximum, beyond its 365-second Rickshaw roadblock.

PHP's `PHPRC` and `PHP_INI_SCAN_DIR` environment variables can select
configuration outside the image's normal PHP defaults. Clear those ambient
overrides while retaining normal `php.ini` and scan-directory loading; `php -n`
would disable configuration and can change the workload. Capture the resolved
loaded/scanned INI paths and hashes, effective directive values, active PHP
and Zend modules, and the RPM identities that supply PHP and its extensions at
build time. Re-capture and compare them under the same sanitized environment
before each run. The [PHP configuration documentation](https://www.php.net/manual/en/configuration.file.php)
and [CLI option reference](https://www.php.net/manual/en/features.commandline.options.php)
describe these environment and `-n` behaviors.

Queue stop signals received during `Popen()` and registration of the new process
group; otherwise a signal can arrive after the child exists but before cleanup
knows its identity. Run process-group cleanup from a shared `finally` path for
timeout, signal, and exceptions, and check descendants even if the leader has
already exited. Preserve timeout stdout/stderr for diagnosis. Local PHPBench
regressions covered deadline boundaries, synthetic INI/module/package changes,
a signal at launch handoff, timeout diagnostics, generic-exception cleanup, and
an exited leader with a live descendant. These helper tests do not replace a
rebuilt-userenv or SUT validation.

## 12. Pin and verify native build and runtime inputs

Before any root-running extraction, verify the pinned archive digest and
validate every member path, type, and mode. Reject absolute or traversing paths,
duplicates, symlinks, hard links, special files, and unsafe permission bits.
Extract only validated regular files and directories into a controlled staging
directory; a later inventory check cannot undo writes made outside the source
tree. Before configuring or compiling, compare the exact post-extraction paths,
types, contents, sizes, and executable bits with the archive, and record a
canonical inventory digest. Test bad digests and unsafe members by asserting
that extraction never starts. Source-level tests do not establish that the
Workshop stage orders validation before extraction in a deployed build.

Inspect the native project's compiled configuration and provider defaults in
the pinned source. For OpenSSL 3.3.0, the PTS install uses `./config no-zlib`
and `make`, while the launcher leaves `OPENSSL_CONF` and
`OPENSSL_MODULES` unset. Preserve that compiled-default behavior: remove
ambient `OPENSSL_*` and `LD_*` overrides, keep the default config file
absent when `make install_sw` leaves it absent, and verify the active provider
set. Record and recheck compiled config/provider directories, provider-module
hashes, the binary, and shared libraries. Do not disable normal config loading
as a shortcut; that changes the native runtime behavior.

Calculate framework roadblocks and adapter deadlines from the same native-work
estimate. In this OpenSSL port, RSA4096 performs ten timed phases even though
the selected profile publishes only legacy sign and verify. The runtime helper
therefore counts ten intervals. The client allows expected work plus 90 seconds
to preflight and execute, then 60 more seconds for completion and cleanup; its
expected-plus-150-second deadline is 30 seconds below Rickshaw's
expected-plus-180-second roadblock. Check that relation at both minimum and
maximum parameter values, and have postprocessing reject timing records that
exceed either budget.

Queue signals across `Popen()` and process registration. When `/proc` inspection
fails while the process-group leader is alive, attempt TERM/KILL against its
owned group, record that cleanup could not be verified, and reject the result.
When the leader has already been reaped and `/proc` inspection fails, do not
signal a numeric group ID whose ownership cannot be established; record the
error and reject the result, while recognizing a surviving descendant cannot
be ruled out in that case. The local real-helper tests cover launch-signal
handoff, timeout output retention, exited-leader descendants, and `/proc`
inspection failure as separate cases; they do not cover the combined
exited-leader plus inspection-failure case. The current x86_64 Workshop build
and seven-algorithm Crucible runs validated source integrity, config/provider
observation, RSA sign/verify parsing, and successful process records. The x86_64
Crucible adapter-SIGTERM and native-timeout lifecycle runs also verified
cleanup for live owned groups through the full harness. Cancelling Crucible
itself and the AArch64 build/run remain separate checks.

## Full-harness lifecycle probes and setup timing

Do not assume an engine container appearing means the benchmark process is
ready. In the PHPBench Crucible trials, endpoint deployment and SUT system
information collection took several minutes before `client-start`. Lifecycle
probes need separate bounded waits for the named run container and the exact
adapter/native processes; stream progress events so a container wait is
distinguishable from a process-discovery wait. Bind every probe to the run ID,
container ID, PID start time, command line, and process group before signaling.

For a deterministic native-timeout check, pause only the verified native
process group and let the adapter's own work deadline expire. PHPBench at
10,000,000 iterations has a 185-second runtime estimate, a 260-second native
work cutoff, and a 365-second Rickshaw roadblock. The current adapter sent
TERM, escalated to KILL when the stopped process group did not exit, retained
output/stderr/error files, marked `timed_out=true`, and left no live process.
The adapter SIGTERM trial similarly marked `interrupted=true` and cleaned the
native group. These lifecycle checks complement local helper-process tests.

An intentionally aborted sole sample makes the full Crucible command exit
nonzero and prevents an ordinary result summary. Preserve the adapter
manifest and lifecycle probe record as the test evidence; CDM document
generation can skip the failed iteration while the run still has indexed
failed-attempt data. During one timeout-abort trial, a CDM Node worker logged
an ephemeral-port `EADDRINUSE` error even though indexing reported completion
and documents were present. Record that symptom separately; do not attribute
it to the benchmark or change shared CDM code without independent evidence.
