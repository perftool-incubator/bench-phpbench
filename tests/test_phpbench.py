#!/usr/bin/env python3

import hashlib
import importlib.util
from importlib.machinery import SourceFileLoader
import io
import json
import os
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

POST_PROCESS_PATH = Path(__file__).parents[1] / "phpbench-post-process.py"
POST_PROCESS_SPEC = importlib.util.spec_from_file_location(
    "phpbench_post_process", POST_PROCESS_PATH
)
phpbench_post_process = importlib.util.module_from_spec(POST_PROCESS_SPEC)
POST_PROCESS_SPEC.loader.exec_module(phpbench_post_process)
CLIENT_PATH = Path(__file__).parents[1] / "phpbench-client"
CLIENT_SPEC = importlib.util.spec_from_loader(
    "phpbench_client", SourceFileLoader("phpbench_client", str(CLIENT_PATH))
)
phpbench_client = importlib.util.module_from_spec(CLIENT_SPEC)
CLIENT_SPEC.loader.exec_module(phpbench_client)
BUILDER_PATH = Path(__file__).parents[1] / "phpbench-build.py"
BUILDER_SPEC = importlib.util.spec_from_loader(
    "phpbench_builder", SourceFileLoader("phpbench_builder", str(BUILDER_PATH))
)
phpbench_builder = importlib.util.module_from_spec(BUILDER_SPEC)
BUILDER_SPEC.loader.exec_module(phpbench_builder)
from phpbench_common import (
    BENCHMARK,
    COMPAT_PATCH_SHA256,
    DEFAULT_PARAMETERS,
    EXPECTED_TEST_COUNT,
    EXPECTED_TEST_NAMES,
    PHPBENCH_VERSION,
    PROFILE_ID,
    PROFILE_PATCHED_ARCHIVE_SHA256,
    PROFILE_VERSION,
    SOURCE_BYTES,
    SOURCE_MD5,
    SOURCE_SHA256,
    SOURCE_URL,
    CLIENT_DEADLINE_PADDING_SECONDS,
    MIN_POST_CLEANUP_ROADBLOCK_MARGIN_SECONDS,
    PROCESS_CLEANUP_RESERVE_SECONDS,
    RICKSHAW_RUNTIME_PADDING_SECONDS,
    canonical_json_sha256,
    capture_php_runtime_configuration,
    client_deadline_seconds,
    client_work_deadline_seconds,
    expected_runtime_seconds,
    parse_native_output,
    parse_parameters,
    sanitized_php_environment,
    verify_php_package_provenance,
    verify_php_runtime_configuration,
)


FIXTURES = Path(__file__).parent / "fixtures"
NATIVE_OUTPUT = (FIXTURES / "phpbench-output-success-2000.txt").read_text(
    encoding="utf-8"
)
BENIGN_STDERR = (
    'PHP Warning:  "continue" targeting switch is equivalent to "break". '
    'Did you mean to use "continue 2"? in '
    "/opt/crucible/software/bench-phpbench/0.8.1/tests/test_switch.php on line 36\n"
)


def digest_text(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def php_runtime_configuration_fixture():
    return {
        "sanitized_environment": {"PHPRC": None, "PHP_INI_SCAN_DIR": None},
        "ini_files": {"loaded": None, "scanned": [], "sha256": {}},
        "extensions": {"php": ["Core", "standard"], "zend": []},
        "directives": {"auto_prepend_file": "", "opcache.enable_cli": "0"},
    }


def php_packages_fixture():
    return [
        {"name": "php-cli", "nevra": "php-cli-8.0.30-8.el9_8.x86_64"},
        {"name": "php-common", "nevra": "php-common-8.0.30-8.el9_8.x86_64"},
    ]


def make_valid_manifest(output_text, stderr_text):
    parameters = {"iterations": 2000}
    native = parse_native_output(output_text, 2000, list(EXPECTED_TEST_NAMES))
    build = {
        "architecture": "x86_64",
        "install_root": "/opt/crucible/software/bench-phpbench/0.8.1",
        "test_count": EXPECTED_TEST_COUNT,
        "test_names": list(EXPECTED_TEST_NAMES),
        "php": {
            "version": "8.0.30",
            "binary_sha256": "a" * 64,
            "rpm_nevra": "php-cli-8.0.30-8.el9_8.x86_64",
            "rpm_packages": php_packages_fixture(),
            "rpm_packages_sha256": canonical_json_sha256(php_packages_fixture()),
            "runtime_configuration": php_runtime_configuration_fixture(),
            "runtime_configuration_sha256": canonical_json_sha256(
                php_runtime_configuration_fixture()
            ),
        },
        "file_sha256": {"phpbench.php": "b" * 64},
    }
    return {
        "format_version": 2,
        "benchmark": BENCHMARK,
        "status": "valid",
        "source_archive_sha256": SOURCE_SHA256,
        "parameters": parameters,
        "effective_parameters": parameters,
        "source": {
            "url": SOURCE_URL,
            "version": PHPBENCH_VERSION,
            "archive_sha256": SOURCE_SHA256,
            "archive_md5": SOURCE_MD5,
            "archive_bytes": SOURCE_BYTES,
        },
        "profile_compatibility": {
            "profile_id": PROFILE_ID,
            "profile_version": PROFILE_VERSION,
            "patched_archive_sha256": PROFILE_PATCHED_ARCHIVE_SHA256,
            "patch_sha256": COMPAT_PATCH_SHA256,
        },
        "build_provenance": build,
        "process": {
            "return_code": 0,
            "timed_out": False,
            "interrupted": False,
            "expected_runtime_seconds": expected_runtime_seconds(2000),
            "client_deadline_seconds": client_deadline_seconds(2000),
            "process_wait_timeout_seconds": 50,
            "cleanup_reserve_seconds": PROCESS_CLEANUP_RESERVE_SECONDS,
            "process_group_stopped": True,
            "php_configuration_verified": True,
            "launch_begin_epoch_ns": 1_000_000_000,
            "process_completion_epoch_ns": 6_000_000_000,
            "process_wallclock_seconds": 5.0,
        },
        "native_result": native,
        "stdout_sha256": digest_text(output_text),
        "stderr_sha256": digest_text(stderr_text),
    }


class WorkshopArchiveTests(unittest.TestCase):
    def _write_archive(self, archive_path, members):
        with tarfile.open(archive_path, "w:gz") as archive:
            root = tarfile.TarInfo("phpbench-0.8.1")
            root.type = tarfile.DIRTYPE
            root.mode = 0o755
            archive.addfile(root)
            for name, kind, data, mode in members:
                member = tarfile.TarInfo(name)
                member.type = kind
                member.mode = mode
                if kind == tarfile.REGTYPE:
                    member.size = len(data)
                    archive.addfile(member, io.BytesIO(data))
                else:
                    archive.addfile(member)

    def _pin_test_archive(self, archive_path, sha256=None):
        data = archive_path.read_bytes()
        return mock.patch.multiple(
            phpbench_builder,
            SOURCE_BYTES=len(data),
            SOURCE_SHA256=sha256 or hashlib.sha256(data).hexdigest(),
            SOURCE_MD5=hashlib.md5(data).hexdigest(),
        )

    def test_workshop_recipe_uses_validated_unpack_before_build(self):
        workshop = json.loads(
            (Path(__file__).parents[1] / "workshop.json").read_text(encoding="utf-8")
        )
        source_requirement = next(
            requirement
            for requirement in workshop["requirements"]
            if requirement["name"] == "phpbench_upstream_source"
        )
        commands = source_requirement["source_info"]["commands"]
        self.assertIn("--safe-unpack", commands["unpack"])
        self.assertNotIn("tar -xzf", commands["unpack"])
        self.assertIn("--patch", commands["commands"][0])

    def test_bad_archive_hash_never_reaches_extraction(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            archive = work / "phpbench-0.8.1.tar.gz"
            source_dir = work / "phpbench-0.8.1"
            self._write_archive(
                archive,
                [
                    (
                        "phpbench-0.8.1/tests/test.php",
                        tarfile.REGTYPE,
                        b"<?php echo 1;\n",
                        0o644,
                    )
                ],
            )
            with self._pin_test_archive(archive, sha256="0" * 64), mock.patch.object(
                phpbench_builder, "_extract_validated_entries"
            ) as extract:
                with self.assertRaisesRegex(RuntimeError, "does not match"):
                    phpbench_builder.safe_unpack_archive(archive, source_dir)
            extract.assert_not_called()
            self.assertFalse(source_dir.exists())

    def test_unsafe_member_never_reaches_extraction(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            archive = work / "phpbench-0.8.1.tar.gz"
            source_dir = work / "phpbench-0.8.1"
            self._write_archive(
                archive,
                [
                    (
                        "phpbench-0.8.1/../../outside.txt",
                        tarfile.REGTYPE,
                        b"do not write this file\n",
                        0o644,
                    )
                ],
            )
            with self._pin_test_archive(archive), mock.patch.object(
                phpbench_builder, "_extract_validated_entries"
            ) as extract:
                with self.assertRaisesRegex(RuntimeError, "unsafe path"):
                    phpbench_builder.safe_unpack_archive(archive, source_dir)
            extract.assert_not_called()
            self.assertFalse(source_dir.exists())
            self.assertFalse((work / "outside.txt").exists())

    def test_safe_unpack_preserves_inventory_and_executable_bits(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            archive = work / "phpbench-0.8.1.tar.gz"
            source_dir = work / "phpbench-0.8.1"
            self._write_archive(
                archive,
                [
                    ("phpbench-0.8.1/tests", tarfile.DIRTYPE, b"", 0o750),
                    (
                        "phpbench-0.8.1/tests/test.php",
                        tarfile.REGTYPE,
                        b"<?php echo 1;\n",
                        0o751,
                    ),
                ],
            )
            with self._pin_test_archive(archive):
                phpbench_builder.safe_unpack_archive(archive, source_dir)
                entries, root_mode = phpbench_builder._validate_archive_only(archive)
                phpbench_builder.verify_unpacked_source(entries, source_dir, root_mode)

            installed = source_dir / "tests/test.php"
            self.assertEqual(installed.read_bytes(), b"<?php echo 1;\n")
            self.assertEqual(installed.stat().st_mode & 0o777, 0o751)
            (source_dir / "extra.txt").write_text("unexpected\n", encoding="utf-8")
            with self._pin_test_archive(archive), self.assertRaisesRegex(
                RuntimeError, "inventory"
            ):
                entries, root_mode = phpbench_builder._validate_archive_only(archive)
                phpbench_builder.verify_unpacked_source(entries, source_dir, root_mode)


class ParameterTests(unittest.TestCase):
    def test_defaults_match_pts_profile_work_count(self):
        self.assertEqual(DEFAULT_PARAMETERS, {"iterations": "2000000"})
        self.assertEqual(parse_parameters([]), {"iterations": 2_000_000})
        self.assertEqual(parse_parameters(["--iterations=2000"]), {"iterations": 2000})

    def test_native_iteration_bounds(self):
        self.assertEqual(parse_parameters(["--iterations=50"])["iterations"], 50)
        self.assertEqual(
            parse_parameters(["--iterations=10000000"])["iterations"], 10_000_000
        )
        for value in ("0", "49", "10000001", "2e6", "1.5", "", "+50"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_parameters(["--iterations=" + value])

    def test_unknown_duplicate_and_non_key_value_arguments_fail(self):
        invalid = [
            ["--threads=2"],
            ["--iterations=2000", "--iterations=3000"],
            ["iterations=2000"],
            ["--iterations", "2000"],
        ]
        for argv in invalid:
            with self.subTest(argv=argv), self.assertRaises(ValueError):
                parse_parameters(argv)

    def test_runtime_estimate_scales_from_default_and_is_bounded(self):
        self.assertEqual(expected_runtime_seconds(50), 1)
        self.assertEqual(expected_runtime_seconds(2_000_000), 37)
        self.assertEqual(expected_runtime_seconds(10_000_000), 185)


class DeadlineBudgetTests(unittest.TestCase):
    def test_supported_range_stays_inside_engine_roadblock_with_cleanup_margin(self):
        for iterations in (50, 2_000_000, 10_000_000):
            with self.subTest(iterations=iterations):
                expected = expected_runtime_seconds(iterations)
                deadline = client_deadline_seconds(iterations)
                work_budget = client_work_deadline_seconds(iterations)
                roadblock = expected + RICKSHAW_RUNTIME_PADDING_SECONDS
                self.assertEqual(deadline, expected + CLIENT_DEADLINE_PADDING_SECONDS)
                self.assertEqual(
                    work_budget,
                    deadline - PROCESS_CLEANUP_RESERVE_SECONDS,
                )
                self.assertGreaterEqual(
                    roadblock - deadline - PROCESS_CLEANUP_RESERVE_SECONDS,
                    MIN_POST_CLEANUP_ROADBLOCK_MARGIN_SECONDS,
                )


class PHPConfigurationProvenanceTests(unittest.TestCase):
    def _probe_payload(self, loaded_ini, scanned_ini, modules=None):
        return {
            "loaded_ini_file": str(loaded_ini),
            "scanned_ini_files": [str(scanned_ini)],
            "php_extensions": modules or ["Core", "standard"],
            "zend_extensions": [],
            "directives": {"auto_prepend_file": "", "opcache.enable_cli": "0"},
        }

    def test_php_environment_sanitizes_overrides_without_disabling_php_ini(self):
        environment = sanitized_php_environment(
            {
                "PATH": "/usr/bin",
                "PHPRC": "/tmp/alternate-php.ini",
                "PHP_INI_SCAN_DIR": "/tmp/alternate-php.d",
                "OTHER": "retained",
            }
        )
        self.assertNotIn("PHPRC", environment)
        self.assertNotIn("PHP_INI_SCAN_DIR", environment)
        self.assertEqual(environment["OTHER"], "retained")
        self.assertEqual(environment["LC_ALL"], "C")
        self.assertEqual(environment["TZ"], "UTC")

    def test_config_capture_hashes_default_ini_files_and_records_modules(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            loaded_ini = work / "php.ini"
            scanned_ini = work / "20-extension.ini"
            loaded_ini.write_text("memory_limit=128M\n", encoding="utf-8")
            scanned_ini.write_text("extension=example.so\n", encoding="utf-8")
            payload = self._probe_payload(loaded_ini, scanned_ini)
            environment = {
                "PATH": "/usr/bin",
                "PHPRC": "/tmp/untrusted.ini",
                "PHP_INI_SCAN_DIR": "/tmp/untrusted.d",
            }
            with mock.patch(
                "phpbench_common.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    ["php"], 0, json.dumps(payload), ""
                ),
            ) as run:
                captured = capture_php_runtime_configuration("/usr/bin/php", environment)
            self.assertEqual(captured["ini_files"]["loaded"], str(loaded_ini.resolve()))
            self.assertEqual(captured["ini_files"]["scanned"], [str(scanned_ini.resolve())])
            self.assertEqual(
                captured["extensions"], {"php": ["Core", "standard"], "zend": []}
            )
            self.assertEqual(
                captured["sanitized_environment"],
                {"PHPRC": None, "PHP_INI_SCAN_DIR": None},
            )
            command = run.call_args.args[0]
            self.assertEqual(command[1], "-r")
            self.assertNotIn("-n", command)
            self.assertNotIn("PHPRC", run.call_args.kwargs["env"])
            self.assertNotIn("PHP_INI_SCAN_DIR", run.call_args.kwargs["env"])

            loaded_ini.write_text("memory_limit=256M\n", encoding="utf-8")
            with mock.patch(
                "phpbench_common.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    ["php"], 0, json.dumps(payload), ""
                ),
            ):
                changed = capture_php_runtime_configuration("/usr/bin/php", environment)
            self.assertNotEqual(
                captured["ini_files"]["sha256"], changed["ini_files"]["sha256"]
            )

    def test_module_ini_and_package_tampering_fails_provenance_comparison(self):
        configuration = php_runtime_configuration_fixture()
        config_hash = canonical_json_sha256(configuration)
        changed_modules = json.loads(json.dumps(configuration))
        changed_modules["extensions"]["php"].append("opcache")
        changed_modules["extensions"]["php"].sort()
        with self.assertRaisesRegex(ValueError, "differ from Workshop"):
            verify_php_runtime_configuration(
                configuration, changed_modules, config_hash
            )

        changed_ini_hash = json.loads(json.dumps(configuration))
        changed_ini_hash["directives"]["memory_limit"] = "256M"
        with self.assertRaisesRegex(ValueError, "differ from Workshop"):
            verify_php_runtime_configuration(
                configuration, changed_ini_hash, config_hash
            )

        packages = php_packages_fixture()
        package_hash = canonical_json_sha256(packages)
        changed_packages = json.loads(json.dumps(packages))
        changed_packages[0]["nevra"] = "php-cli-8.0.30-9.el9_8.x86_64"
        with self.assertRaisesRegex(ValueError, "differ from Workshop"):
            verify_php_package_provenance(packages, changed_packages, package_hash)


class ProcessLifecycleTests(unittest.TestCase):
    def test_signal_during_launch_handoff_is_queued_until_registration(self):
        old_handler = signal.getsignal(signal.SIGTERM)
        old_active = phpbench_client.ACTIVE_PROCESS
        old_interrupted = phpbench_client.INTERRUPTED_BY
        old_launch = phpbench_client.LAUNCH_IN_PROGRESS
        old_pending = phpbench_client.PENDING_LAUNCH_SIGNAL
        handler_observations = []

        def recording_handler(signum, frame):
            handler_observations.append(
                (
                    phpbench_client.LAUNCH_IN_PROGRESS,
                    phpbench_client.ACTIVE_PROCESS is None,
                )
            )
            phpbench_client.handle_signal(signum, frame)

        real_popen = subprocess.Popen

        def spawn_then_signal(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            os.kill(os.getpid(), signal.SIGTERM)
            return process

        signal.signal(signal.SIGTERM, recording_handler)
        try:
            with mock.patch.object(
                phpbench_client.subprocess, "Popen", side_effect=spawn_then_signal
            ):
                with self.assertRaises(phpbench_client.RunInterrupted):
                    phpbench_client.start_owned_process(
                        [sys.executable, "-c", "import time; time.sleep(60)"],
                        Path.cwd(),
                        sanitized_php_environment(),
                    )
            self.assertEqual(handler_observations, [(True, True)])
            self.assertIsNotNone(phpbench_client.ACTIVE_PROCESS)
        finally:
            active = phpbench_client.ACTIVE_PROCESS
            if active is not None:
                self.assertTrue(phpbench_client.terminate_process_group(active))
                active.communicate(timeout=2)
            phpbench_client.ACTIVE_PROCESS = old_active
            phpbench_client.INTERRUPTED_BY = old_interrupted
            phpbench_client.LAUNCH_IN_PROGRESS = old_launch
            phpbench_client.PENDING_LAUNCH_SIGNAL = old_pending
            signal.signal(signal.SIGTERM, old_handler)

    def test_timeout_captures_diagnostics_and_kills_process_group(self):
        script = (
            "import os,time; print('timeout-diagnostic',flush=True); "
            "child=os.fork(); time.sleep(60)"
        )
        process = subprocess.Popen(
            [sys.executable, "-c", script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            (stdout, _stderr), timed_out = phpbench_client.communicate_owned_process(
                process, 0.2, time.monotonic() + 3
            )
            self.assertTrue(timed_out)
            self.assertIn("timeout-diagnostic", stdout)
            self.assertEqual(process.returncode, -signal.SIGTERM)
            self.assertFalse(phpbench_client.process_group_has_live_members(process.pid))
        finally:
            phpbench_client.terminate_process_group(process)
            process.communicate(timeout=2)

    def test_unexpected_supervision_error_runs_final_cleanup_and_retains_output(self):
        old_active = phpbench_client.ACTIVE_PROCESS
        real_start = phpbench_client.start_owned_process
        build = {
            "php": {"binary": sys.executable, "version": "8.0.30"},
            "source": {},
            "profile_compatibility": {},
            "architecture": "x86_64",
            "install_root": "/opt/crucible/software/bench-phpbench/0.8.1",
            "test_count": EXPECTED_TEST_COUNT,
            "test_names": list(EXPECTED_TEST_NAMES),
            "file_sha256": {"phpbench.php": "a" * 64},
        }
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            script = "import time; time.sleep(60)"

            def start_sleeper(_command, _cwd, environment):
                return real_start(
                    [sys.executable, "-c", script], Path.cwd(), environment
                )

            try:
                with mock.patch.object(phpbench_client, "OUTPUT", work / "stdout.txt"), mock.patch.object(
                    phpbench_client, "STDERR", work / "stderr.txt"
                ), mock.patch.object(phpbench_client, "MANIFEST", work / "manifest.json"), mock.patch.object(
                    phpbench_client, "ERROR", work / "error.txt"
                ), mock.patch.object(
                    phpbench_client, "load_build_manifest", return_value=build
                ), mock.patch.object(
                    phpbench_client, "start_owned_process", side_effect=start_sleeper
                ), mock.patch.object(
                    phpbench_client,
                    "communicate_owned_process",
                    side_effect=RuntimeError("injected supervision error"),
                ), mock.patch.object(sys, "stderr", io.StringIO()):
                    self.assertEqual(phpbench_client.main([]), 1)
                manifest = json.loads((work / "manifest.json").read_text())
                self.assertEqual(manifest["status"], "invalid")
                self.assertIn("injected supervision error", manifest["error"])
                self.assertTrue(manifest["process"]["process_group_stopped"])
                self.assertIsNone(phpbench_client.ACTIVE_PROCESS)
            finally:
                active = phpbench_client.ACTIVE_PROCESS
                if active is not None:
                    phpbench_client.terminate_process_group(active)
                    active.communicate(timeout=2)
                phpbench_client.ACTIVE_PROCESS = old_active

    def test_cleanup_finds_descendants_after_the_group_leader_exits(self):
        script = (
            "import os,time\n"
            "child=os.fork()\n"
            "if child == 0:\n"
            " os.close(1); os.close(2); time.sleep(60); os._exit(0)\n"
            "print('leader-exited', flush=True)\n"
        )
        process = subprocess.Popen(
            [sys.executable, "-c", script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            stdout, _stderr = process.communicate(timeout=3)
            self.assertIn("leader-exited", stdout)
            self.assertIsNotNone(process.returncode)
            self.assertTrue(phpbench_client.process_group_has_live_members(process.pid))
            self.assertTrue(phpbench_client.terminate_process_group(process))
            self.assertFalse(phpbench_client.process_group_has_live_members(process.pid))
        finally:
            phpbench_client.terminate_process_group(process)


class NativeOutputTests(unittest.TestCase):
    def test_captured_native_output_has_one_row_for_every_profile_test(self):
        parsed = parse_native_output(NATIVE_OUTPUT, 2000, list(EXPECTED_TEST_NAMES))
        self.assertEqual(parsed["version"], "0.8.1")
        self.assertEqual(parsed["php_version"], "8.0.30")
        self.assertEqual(parsed["test_count"], 56)
        self.assertEqual(parsed["iterations"], 2000)
        self.assertEqual(parsed["score"], 539363)
        self.assertEqual(parsed["test_names"], list(EXPECTED_TEST_NAMES))
        self.assertEqual(len(parsed["test_durations"]), 56)

    def test_missing_or_duplicate_timed_test_rows_fail(self):
        lines = NATIVE_OUTPUT.splitlines()
        row_index = next(i for i, line in enumerate(lines) if " seconds." in line)
        row = lines[row_index]
        missing = "\n".join(lines[:row_index] + lines[row_index + 1 :]) + "\n"
        duplicated = "\n".join(lines[:row_index] + [row, row] + lines[row_index + 1 :]) + "\n"
        for output in (missing, duplicated):
            with self.subTest(output=output[:80]), self.assertRaises(ValueError):
                parse_native_output(output, 2000, list(EXPECTED_TEST_NAMES))

    def test_wrong_iteration_summary_and_missing_score_fail(self):
        bad_iteration = NATIVE_OUTPUT.replace("Iterations : 2000", "Iterations : 2001")
        missing_score = NATIVE_OUTPUT.replace("Score      : 539363 (higher is better)", "")
        for output in (bad_iteration, missing_score):
            with self.subTest(output=output[-80:]), self.assertRaises(ValueError):
                parse_native_output(output, 2000, list(EXPECTED_TEST_NAMES))


class PostprocessValidationTests(unittest.TestCase):
    def test_main_uses_native_score_metric_and_sum_aggregation(self):
        manifest = make_valid_manifest(NATIVE_OUTPUT, BENIGN_STDERR)
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            output_path = work / "phpbench-output.txt"
            stderr_path = work / "phpbench-stderr.txt"
            manifest_path = work / "phpbench-run-manifest.json"
            output_path.write_text(NATIVE_OUTPUT, encoding="utf-8")
            stderr_path.write_text(BENIGN_STDERR, encoding="utf-8")
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            class CapturingCDMMetrics:
                instances = []

                def __init__(self):
                    self.records = []
                    self.instances.append(self)

                def log_sample(self, file_id, descriptor, names, sample):
                    self.records.append((file_id, descriptor, names, sample))

                def finish_samples(self):
                    return "metric-data-0"

            previous = Path.cwd()
            try:
                os.chdir(work)
                with mock.patch.object(phpbench_post_process, "OUTPUT", output_path), mock.patch.object(
                    phpbench_post_process, "STDERR", stderr_path
                ), mock.patch.object(phpbench_post_process, "MANIFEST", manifest_path), mock.patch.object(
                    phpbench_post_process,
                    "load_cdm_metrics",
                    return_value=CapturingCDMMetrics,
                ):
                    phpbench_post_process.main(["--iterations=2000"])
            finally:
                os.chdir(previous)

            self.assertEqual(len(CapturingCDMMetrics.instances), 1)
            file_id, descriptor, names, sample = CapturingCDMMetrics.instances[0].records[0]
            self.assertEqual(file_id, "0")
            self.assertEqual(descriptor, {
                "source": "phpbench",
                "class": "throughput",
                "type": "phpbench-score",
                "default-aggregation": "sum",
            })
            self.assertEqual(names["job"], "PHPBench 0.8.1")
            self.assertEqual(sample["value"], 539363)
            emitted = json.loads((work / "postprocess/post-process-data.json").read_text())
            self.assertEqual(emitted["primary-metric"], "phpbench-score")

    def test_sample_accepts_profile_warning_but_checks_output_manifest(self):
        manifest = make_valid_manifest(NATIVE_OUTPUT, BENIGN_STDERR)
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "phpbench-output.txt"
            stderr_path = Path(directory) / "phpbench-stderr.txt"
            output_path.write_text(NATIVE_OUTPUT, encoding="utf-8")
            stderr_path.write_text(BENIGN_STDERR, encoding="utf-8")
            with mock.patch.object(phpbench_post_process, "OUTPUT", output_path), mock.patch.object(
                phpbench_post_process, "STDERR", stderr_path
            ):
                result = phpbench_post_process.validate_sample(
                    manifest,
                    {"iterations": 2000},
                    NATIVE_OUTPUT,
                    BENIGN_STDERR,
                )
        self.assertEqual(result["score"], 539363)
        self.assertEqual(result["test_count"], 56)
        self.assertEqual(result["end_ms"] - result["begin_ms"], 5000)

    def test_manifest_and_fatal_stderr_mismatches_fail(self):
        for mutation in (
            "parameters",
            "status",
            "source",
            "php-config",
            "php-package",
            "deadline",
        ):
            manifest = make_valid_manifest(NATIVE_OUTPUT, BENIGN_STDERR)
            if mutation == "parameters":
                manifest["parameters"]["iterations"] = 3000
            elif mutation == "status":
                manifest["status"] = "invalid"
            elif mutation == "source":
                manifest["source"]["version"] = "0.8.0"
            elif mutation == "php-config":
                manifest["build_provenance"]["php"]["runtime_configuration"][
                    "directives"
                ]["memory_limit"] = "256M"
            elif mutation == "php-package":
                manifest["build_provenance"]["php"]["rpm_packages"][0][
                    "nevra"
                ] = "php-cli-8.0.30-9.el9_8.x86_64"
            else:
                manifest["process"]["client_deadline_seconds"] += 1
            with self.subTest(mutation=mutation):
                with tempfile.TemporaryDirectory() as directory:
                    output_path = Path(directory) / "phpbench-output.txt"
                    stderr_path = Path(directory) / "phpbench-stderr.txt"
                    output_path.write_text(NATIVE_OUTPUT, encoding="utf-8")
                    stderr_path.write_text(BENIGN_STDERR, encoding="utf-8")
                    with mock.patch.object(phpbench_post_process, "OUTPUT", output_path), mock.patch.object(
                        phpbench_post_process, "STDERR", stderr_path
                    ):
                        with self.assertRaises(ValueError):
                            phpbench_post_process.validate_sample(
                                manifest,
                                {"iterations": 2000},
                                NATIVE_OUTPUT,
                                BENIGN_STDERR,
                            )

        manifest = make_valid_manifest(NATIVE_OUTPUT, "PHP Fatal error: broken\n")
        fatal_stderr = "PHP Fatal error: broken\n"
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "phpbench-output.txt"
            stderr_path = Path(directory) / "phpbench-stderr.txt"
            output_path.write_text(NATIVE_OUTPUT, encoding="utf-8")
            stderr_path.write_text(fatal_stderr, encoding="utf-8")
            with mock.patch.object(phpbench_post_process, "OUTPUT", output_path), mock.patch.object(
                phpbench_post_process, "STDERR", stderr_path
            ):
                with self.assertRaises(ValueError):
                    phpbench_post_process.validate_sample(
                        manifest,
                        {"iterations": 2000},
                        NATIVE_OUTPUT,
                        fatal_stderr,
                    )


if __name__ == "__main__":
    unittest.main()
