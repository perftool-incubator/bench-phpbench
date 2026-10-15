#!/usr/bin/env python3
# -*- mode: python; indent-tabs-mode: nil; python-indent-level: 4 -*-
# vim: autoindent tabstop=4 shiftwidth=4 expandtab softtabstop=4 filetype=python

"""Revalidate a complete PHPBench sample and emit its Crucible score metric."""

import json
import math
import os
import re
import sys
from pathlib import Path

from phpbench_common import (
    BENCHMARK,
    COMPAT_PATCH_SHA256,
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
    SUPPORTED_ARCHITECTURES,
    PROCESS_CLEANUP_RESERVE_SECONDS,
    canonical_json_sha256,
    client_deadline_seconds,
    client_work_deadline_seconds,
    expected_runtime_seconds,
    parse_native_output,
    parse_parameters,
    sha256_file,
    validate_php_package_provenance,
    validate_php_runtime_configuration,
)


OUTPUT = Path("phpbench-output.txt")
STDERR = Path("phpbench-stderr.txt")
MANIFEST = Path("phpbench-run-manifest.json")
NANOSECONDS_PER_MILLISECOND = 1_000_000


def required_positive_int(value, label):
    if type(value) is not int or value <= 0:
        raise ValueError("PHPBench manifest %s is missing or invalid" % label)
    return value


def validate_sample(manifest, parameters, output_text, stderr_text):
    if not isinstance(manifest, dict) or manifest.get("format_version") != 2 or (
        manifest.get("status") != "valid" or manifest.get("benchmark") != BENCHMARK
    ):
        raise ValueError("PHPBench run manifest does not mark this sample valid")
    if manifest.get("parameters") != parameters:
        raise ValueError("PHPBench manifest parameters differ from the submitted values")
    if manifest.get("effective_parameters") != parameters:
        raise ValueError("PHPBench effective parameters differ from the submitted values")
    if manifest.get("source_archive_sha256") != SOURCE_SHA256:
        raise ValueError("PHPBench run manifest does not match the pinned source archive")
    source = manifest.get("source")
    if not isinstance(source, dict) or source != {
        "url": SOURCE_URL,
        "version": PHPBENCH_VERSION,
        "archive_sha256": SOURCE_SHA256,
        "archive_md5": SOURCE_MD5,
        "archive_bytes": SOURCE_BYTES,
    }:
        raise ValueError("PHPBench run manifest has unexpected upstream source provenance")
    profile = manifest.get("profile_compatibility")
    if not isinstance(profile, dict) or profile != {
        "profile_id": PROFILE_ID,
        "profile_version": PROFILE_VERSION,
        "patched_archive_sha256": PROFILE_PATCHED_ARCHIVE_SHA256,
        "patch_sha256": COMPAT_PATCH_SHA256,
    }:
        raise ValueError("PHPBench run manifest has unexpected compatibility patch provenance")

    build = manifest.get("build_provenance")
    if not isinstance(build, dict) or (
        build.get("architecture") not in SUPPORTED_ARCHITECTURES
        or build.get("install_root") != "/opt/crucible/software/bench-phpbench/0.8.1"
        or build.get("test_count") != EXPECTED_TEST_COUNT
        or build.get("test_names") != list(EXPECTED_TEST_NAMES)
    ):
        raise ValueError("PHPBench Workshop build provenance is missing or invalid")
    php = build.get("php")
    if not isinstance(php, dict) or (
        not isinstance(php.get("version"), str)
        or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", php["version"])
        or not re.fullmatch(r"[0-9a-f]{64}", str(php.get("binary_sha256", "")))
        or not isinstance(php.get("rpm_nevra"), str)
        or php.get("version") != manifest.get("native_result", {}).get("php_version")
    ):
        raise ValueError("PHP CLI provenance does not match the native output")
    runtime_configuration = php.get("runtime_configuration")
    validate_php_runtime_configuration(runtime_configuration)
    if canonical_json_sha256(runtime_configuration) != php.get(
        "runtime_configuration_sha256"
    ):
        raise ValueError("PHP runtime configuration hash does not match its provenance")
    php_packages = php.get("rpm_packages")
    validate_php_package_provenance(php_packages)
    if canonical_json_sha256(php_packages) != php.get("rpm_packages_sha256"):
        raise ValueError("PHP RPM package hash does not match its provenance")
    if not any(
        item.get("name") == "php-cli" and item.get("nevra") == php.get("rpm_nevra")
        for item in php_packages
    ):
        raise ValueError("PHP CLI RPM identity differs from the package inventory")
    file_hashes = build.get("file_sha256")
    if (
        not isinstance(file_hashes, dict)
        or not file_hashes
        or any(not re.fullmatch(r"[0-9a-f]{64}", str(value)) for value in file_hashes.values())
    ):
        raise ValueError("PHPBench installed source hashes are invalid")

    if sha256_file(OUTPUT) != manifest.get("stdout_sha256"):
        raise ValueError("PHPBench stdout does not match the run manifest")
    if sha256_file(STDERR) != manifest.get("stderr_sha256"):
        raise ValueError("PHPBench stderr does not match the run manifest")
    if re.search(r"PHP (?:Fatal error|Parse error)|Uncaught (?:Error|Exception)", stderr_text):
        raise ValueError("PHPBench stderr contains a fatal PHP error")

    parsed = parse_native_output(output_text, parameters["iterations"], build["test_names"])
    if parsed != manifest.get("native_result"):
        raise ValueError("PHPBench native summary does not match its retained stdout")

    process = manifest.get("process")
    if not isinstance(process, dict) or (
        process.get("return_code") != 0
        or process.get("timed_out") is not False
        or process.get("interrupted") is not False
        or process.get("expected_runtime_seconds")
        != expected_runtime_seconds(parameters["iterations"])
        or process.get("client_deadline_seconds")
        != client_deadline_seconds(parameters["iterations"])
        or process.get("cleanup_reserve_seconds") != PROCESS_CLEANUP_RESERVE_SECONDS
        or process.get("process_group_stopped") is not True
        or process.get("php_configuration_verified") is not True
    ):
        raise ValueError("PHPBench native process did not complete cleanly")
    wait_timeout = process.get("process_wait_timeout_seconds")
    if (
        isinstance(wait_timeout, bool)
        or not isinstance(wait_timeout, (int, float))
        or not math.isfinite(wait_timeout)
        or wait_timeout <= 0
        or wait_timeout > client_work_deadline_seconds(parameters["iterations"])
    ):
        raise ValueError("PHPBench native process wait deadline is invalid")
    launch_ns = required_positive_int(process.get("launch_begin_epoch_ns"), "launch time")
    completion_ns = required_positive_int(
        process.get("process_completion_epoch_ns"), "completion time"
    )
    if completion_ns <= launch_ns:
        raise ValueError("PHPBench process timestamps are not ordered")
    elapsed = process.get("process_wallclock_seconds")
    if (
        isinstance(elapsed, bool)
        or not isinstance(elapsed, (int, float))
        or not math.isfinite(elapsed)
        or elapsed <= 0
    ):
        raise ValueError("PHPBench process wall-clock duration is invalid")
    measured_elapsed = (completion_ns - launch_ns) / 1_000_000_000
    tolerance = max(0.010, elapsed * 0.001)
    if abs(measured_elapsed - elapsed) > tolerance:
        raise ValueError("PHPBench monotonic and epoch process durations disagree")

    return {
        "score": parsed["score"],
        "iterations": parameters["iterations"],
        "test_count": parsed["test_count"],
        "begin_ms": launch_ns // NANOSECONDS_PER_MILLISECOND,
        "end_ms": (
            completion_ns + NANOSECONDS_PER_MILLISECOND - 1
        )
        // NANOSECONDS_PER_MILLISECOND,
    }


def load_cdm_metrics():
    toolbox_home = os.environ.get("TOOLBOX_HOME")
    if not toolbox_home:
        raise ValueError("PHPBench post-processing requires TOOLBOX_HOME")
    toolbox_python = Path(toolbox_home) / "python"
    if not toolbox_python.is_dir():
        raise ValueError("PHPBench post-processing cannot find %s" % toolbox_python)
    if str(toolbox_python) not in sys.path:
        sys.path.append(str(toolbox_python))
    from toolbox.cdm_metrics import CDMMetrics

    return CDMMetrics


def main(argv):
    output_dir = Path("postprocess")
    (output_dir / "post-process-data.json").unlink(missing_ok=True)
    parameters = parse_parameters(argv)
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    output_text = OUTPUT.read_text(encoding="utf-8", errors="replace")
    stderr_text = STDERR.read_text(encoding="utf-8", errors="replace")
    result = validate_sample(manifest, parameters, output_text, stderr_text)

    metrics = load_cdm_metrics()()
    metrics.log_sample(
        "0",
        {
            "source": BENCHMARK,
            "class": "throughput",
            "type": "phpbench-score",
            "default-aggregation": "sum",
        },
        {
            "job": "PHPBench 0.8.1",
            "desc": (
                "native aggregate suite score; higher is better; %d tests; "
                "iterations=%d; timestamps bound the PHP process, not the test timer"
                % (result["test_count"], result["iterations"])
            ),
        },
        {
            "begin": result["begin_ms"],
            "end": result["end_ms"],
            "value": result["score"],
        },
    )
    metric_file = metrics.finish_samples()
    output_dir.mkdir(exist_ok=True)
    postprocess = {
        "rickshaw-bench-metric": {"schema": {"version": "2021.04.12"}},
        "benchmark": BENCHMARK,
        "primary-period": "score-support",
        "primary-metric": "phpbench-score",
        "periods": [{"name": "score-support", "metric-files": [metric_file]}],
    }
    (output_dir / "post-process-data.json").write_text(
        json.dumps(postprocess, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except Exception as exc:
        print("PHPBench post-processing failed: %s" % exc, file=sys.stderr)
        sys.exit(1)
