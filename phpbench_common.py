#!/usr/bin/env python3
# -*- mode: python; indent-tabs-mode: nil; python-indent-level: 4 -*-
# vim: autoindent tabstop=4 shiftwidth=4 expandtab softtabstop=4 filetype=python

"""PHPBench workload identity, parameter parsing, and native-output checks."""

import hashlib
import json
import math
import os
import platform
import re
import subprocess
import time
from pathlib import Path


BENCHMARK = "phpbench"
PHPBENCH_VERSION = "0.8.1"
SOURCE_URL = "https://ftp.ntu.edu.tw/pure-ftpd/misc/phpbench/phpbench-0.8.1.tar.gz"
SOURCE_SHA256 = "deff97dd1372405dbad3bf633009ca8446cb9770c5effa40cc45849a461c1b4a"
SOURCE_MD5 = "9ee2e4305e320c7f20a3d81978c8d0dc"
SOURCE_BYTES = 9482
PROFILE_ID = "pts/phpbench"
PROFILE_VERSION = "1.1.6"
PROFILE_PATCHED_ARCHIVE_SHA256 = "32503bd4ace0c8429493de864ca48bb16febed867e52b75f4369d7145f797718"
COMPAT_PATCH_SHA256 = "0fd949928a854134ac40561762c133f9c55edefd9c60f7a072cf680f3209d7e5"
EXPECTED_TEST_COUNT = 56
EXPECTED_TEST_NAMES = (
    "test_arithmetic",
    "test_array_operators",
    "test_bitwise",
    "test_casting",
    "test_chr_fixed",
    "test_chr_hardcoded",
    "test_chr_var",
    "test_comment_loop",
    "test_compare",
    "test_compare_false",
    "test_compare_invert",
    "test_compare_strict",
    "test_compare_unstrict",
    "test_constants",
    "test_crc32",
    "test_do_while",
    "test_do_while_break",
    "test_empty",
    "test_empty_loop",
    "test_ereg",
    "test_foreach",
    "test_get_class",
    "test_global_scalar_assign",
    "test_global_string_assign",
    "test_if_constant",
    "test_increment",
    "test_is_array",
    "test_is_object",
    "test_is_type",
    "test_isset",
    "test_line",
    "test_local_array_assign",
    "test_local_boolean_assign",
    "test_local_float_assign",
    "test_local_hash_assign",
    "test_local_integer_assign",
    "test_local_object_assign",
    "test_local_scalar_assign",
    "test_local_string_assign",
    "test_md5",
    "test_microtime",
    "test_mt_rand",
    "test_ord",
    "test_ordered_functions",
    "test_ordered_functions_references",
    "test_preg_match",
    "test_rand",
    "test_references",
    "test_sha1",
    "test_string_append",
    "test_strlen",
    "test_switch",
    "test_time",
    "test_unordered_functions",
    "test_variable_variables",
    "test_while",
)
SUPPORTED_ARCHITECTURES = {"x86_64"}
DEFAULT_PARAMETERS = {"iterations": "2000000"}
MIN_ITERATIONS = 50
MAX_ITERATIONS = 10_000_000
BASELINE_ITERATIONS = 2_000_000
BASELINE_RUNTIME_SECONDS = 37
RICKSHAW_RUNTIME_PADDING_SECONDS = 180
CLIENT_DEADLINE_PADDING_SECONDS = 90
PROCESS_CLEANUP_RESERVE_SECONDS = 15
MIN_POST_CLEANUP_ROADBLOCK_MARGIN_SECONDS = 60
PHP_CONFIGURATION_ENVIRONMENT_VARIABLES = ("PHPRC", "PHP_INI_SCAN_DIR")
INSTALL_ROOT = Path("/opt/crucible/software/bench-phpbench/0.8.1")
BUILD_MANIFEST = INSTALL_ROOT / "build-manifest.json"

PHP_CONFIGURATION_PROBE = r'''$loaded = php_ini_loaded_file();
$scanned = php_ini_scanned_files();
$scanned = ($scanned === false || $scanned === null) ? array() : array_values(array_filter(array_map('trim', explode(',', $scanned)), 'strlen'));
$modules = get_loaded_extensions(false);
$zend = get_loaded_extensions(true);
sort($modules, SORT_STRING);
sort($zend, SORT_STRING);
$directives = ini_get_all(null, false);
if ($directives === false) { throw new RuntimeException('ini_get_all failed'); }
ksort($directives, SORT_STRING);
$payload = array(
    'loaded_ini_file' => ($loaded === false || $loaded === '') ? null : $loaded,
    'scanned_ini_files' => $scanned,
    'php_extensions' => $modules,
    'zend_extensions' => $zend,
    'directives' => $directives
);
echo json_encode($payload, JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR);
'''

TEST_ROW_RE = re.compile(
    r"^\s*(test_[A-Za-z0-9_]+)\s+([0-9]+\.[0-9]{4}) seconds\.$"
)


def sha256_file(path, deadline_monotonic=None):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
                raise TimeoutError("PHPBench provenance hashing exceeded its work deadline")
            digest.update(block)
    return digest.hexdigest()


def canonical_json_sha256(value):
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def sanitized_php_environment(base_environment=None):
    """Keep PHP's normal default config search while removing ambient overrides."""
    environment = dict(os.environ if base_environment is None else base_environment)
    for variable in PHP_CONFIGURATION_ENVIRONMENT_VARIABLES:
        environment.pop(variable, None)
    environment["LC_ALL"] = "C"
    environment["TZ"] = "UTC"
    return environment


def _command_timeout(deadline_monotonic):
    if deadline_monotonic is None:
        return 30
    remaining = deadline_monotonic - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("PHPBench provenance check exceeded its work deadline")
    return min(30, remaining)


def validate_php_runtime_configuration(configuration):
    """Reject incomplete or malformed PHP config/module provenance records."""
    if not isinstance(configuration, dict):
        raise ValueError("PHP runtime configuration provenance is missing")
    if configuration.get("sanitized_environment") != {
        "PHPRC": None,
        "PHP_INI_SCAN_DIR": None,
    }:
        raise ValueError("PHP configuration override variables were not sanitized")
    ini_files = configuration.get("ini_files")
    if not isinstance(ini_files, dict):
        raise ValueError("PHP INI file provenance is missing")
    loaded = ini_files.get("loaded")
    scanned = ini_files.get("scanned")
    hashes = ini_files.get("sha256")
    if loaded is not None and (not isinstance(loaded, str) or not Path(loaded).is_absolute()):
        raise ValueError("PHP loaded INI path is invalid")
    if not isinstance(scanned, list) or any(
        not isinstance(path, str) or not Path(path).is_absolute() for path in scanned
    ):
        raise ValueError("PHP scanned INI paths are invalid")
    if len(set(scanned)) != len(scanned):
        raise ValueError("PHP scanned INI paths contain duplicates")
    expected_paths = set(scanned)
    if loaded is not None:
        expected_paths.add(loaded)
    if not isinstance(hashes, dict) or set(hashes) != expected_paths:
        raise ValueError("PHP INI file hashes do not cover the effective config")
    if any(not re.fullmatch(r"[0-9a-f]{64}", str(value)) for value in hashes.values()):
        raise ValueError("PHP INI file hash is malformed")
    extensions = configuration.get("extensions")
    if not isinstance(extensions, dict):
        raise ValueError("PHP extension provenance is missing")
    for key in ("php", "zend"):
        values = extensions.get(key)
        if (
            not isinstance(values, list)
            or any(not isinstance(item, str) or not item for item in values)
            or values != sorted(set(values))
        ):
            raise ValueError("PHP %s extension list is invalid" % key)
    directives = configuration.get("directives")
    if not isinstance(directives, dict) or any(
        not isinstance(name, str) for name in directives
    ):
        raise ValueError("PHP effective INI directive map is invalid")


def verify_php_runtime_configuration(expected, actual, expected_sha256):
    validate_php_runtime_configuration(expected)
    validate_php_runtime_configuration(actual)
    if canonical_json_sha256(expected) != expected_sha256:
        raise ValueError("PHP runtime configuration provenance hash is invalid")
    if actual != expected:
        raise ValueError("Effective PHP INI files, directives, or modules differ from Workshop")


def capture_php_runtime_configuration(
    php_binary, base_environment=None, deadline_monotonic=None
):
    """Capture the default-loaded INI files, directives, and active PHP modules."""
    environment = sanitized_php_environment(base_environment)
    if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
        raise TimeoutError("PHPBench config capture exceeded its work deadline")
    result = subprocess.run(
        [str(php_binary), "-r", PHP_CONFIGURATION_PROBE],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=environment,
        timeout=_command_timeout(deadline_monotonic),
        check=False,
    )
    if result.returncode != 0:
        raise ValueError("PHP runtime configuration probe failed: %s" % result.stderr)
    try:
        payload = json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("PHP runtime configuration probe emitted invalid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("PHP runtime configuration probe did not emit an object")

    loaded_value = payload.get("loaded_ini_file")
    if loaded_value in (None, False, ""):
        loaded = None
    elif isinstance(loaded_value, str):
        loaded = str(Path(loaded_value).resolve(strict=True))
    else:
        raise ValueError("PHP reported an invalid loaded INI filename")
    scanned_value = payload.get("scanned_ini_files")
    if not isinstance(scanned_value, list) or any(
        not isinstance(path, str) or not path for path in scanned_value
    ):
        raise ValueError("PHP reported an invalid scanned INI file list")
    scanned = [str(Path(path).resolve(strict=True)) for path in scanned_value]
    if len(set(scanned)) != len(scanned):
        raise ValueError("PHP reported duplicate scanned INI files")
    file_hashes = {}
    for path in ([loaded] if loaded is not None else []) + scanned:
        resolved = Path(path)
        if not resolved.is_file():
            raise ValueError("PHP reported a non-file INI path: %s" % path)
        file_hashes[path] = sha256_file(path, deadline_monotonic)

    configuration = {
        "sanitized_environment": {name: None for name in PHP_CONFIGURATION_ENVIRONMENT_VARIABLES},
        "ini_files": {"loaded": loaded, "scanned": scanned, "sha256": file_hashes},
        "extensions": {
            "php": payload.get("php_extensions"),
            "zend": payload.get("zend_extensions"),
        },
        "directives": payload.get("directives"),
    }
    validate_php_runtime_configuration(configuration)
    return configuration


def php_package_provenance(base_environment=None, deadline_monotonic=None):
    """Return installed PHP RPM identities, including separately packaged modules."""
    result = subprocess.run(
        ["rpm", "-qa", "--qf", "%{NAME}\t%{NEVRA}\n"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=sanitized_php_environment(base_environment),
        timeout=_command_timeout(deadline_monotonic),
        check=False,
    )
    if result.returncode != 0:
        raise ValueError("Could not enumerate PHP RPM provenance: %s" % result.stderr)
    packages = []
    for line in result.stdout.splitlines():
        parts = line.split("\t", 1)
        if len(parts) == 2 and (parts[0] == "php" or parts[0].startswith("php-")):
            packages.append({"name": parts[0], "nevra": parts[1]})
    packages.sort(key=lambda item: (item["name"], item["nevra"]))
    if not any(package["name"] == "php-cli" for package in packages):
        raise ValueError("PHP CLI RPM provenance is missing")
    return packages


def validate_php_package_provenance(packages):
    if not isinstance(packages, list) or not packages:
        raise ValueError("PHP RPM package provenance is missing")
    normalized = []
    for package in packages:
        if (
            not isinstance(package, dict)
            or not isinstance(package.get("name"), str)
            or not package["name"].startswith("php")
            or not isinstance(package.get("nevra"), str)
            or not package["nevra"]
        ):
            raise ValueError("PHP RPM package provenance is invalid")
        normalized.append((package["name"], package["nevra"]))
    if normalized != sorted(set(normalized)) or not any(
        name == "php-cli" for name, _nevra in normalized
    ):
        raise ValueError("PHP RPM package list is incomplete or unsorted")


def verify_php_package_provenance(expected, actual, expected_sha256):
    validate_php_package_provenance(expected)
    validate_php_package_provenance(actual)
    if canonical_json_sha256(expected) != expected_sha256:
        raise ValueError("PHP RPM package provenance hash is invalid")
    if actual != expected:
        raise ValueError("Installed PHP RPM packages differ from Workshop provenance")


def client_deadline_seconds(iterations):
    """Whole-client deadline, leaving room before Rickshaw's runtime roadblock."""
    expected = expected_runtime_seconds(iterations)
    client_deadline = expected + CLIENT_DEADLINE_PADDING_SECONDS
    roadblock = expected + RICKSHAW_RUNTIME_PADDING_SECONDS
    remaining_after_cleanup = (
        roadblock - client_deadline - PROCESS_CLEANUP_RESERVE_SECONDS
    )
    if remaining_after_cleanup < MIN_POST_CLEANUP_ROADBLOCK_MARGIN_SECONDS:
        raise ValueError("PHPBench deadline leaves too little Rickshaw roadblock margin")
    return client_deadline


def client_work_deadline_seconds(iterations):
    return client_deadline_seconds(iterations) - PROCESS_CLEANUP_RESERVE_SECONDS


def parse_parameters(argv):
    """Parse the single Crucible runtime parameter and apply its default."""
    values = dict(DEFAULT_PARAMETERS)
    seen = set()
    for item in argv:
        if not item.startswith("--") or "=" not in item:
            raise ValueError("arguments must use --key=value syntax")
        key, value = item[2:].split("=", 1)
        if key not in DEFAULT_PARAMETERS:
            raise ValueError("unsupported PHPBench parameter: %s" % key)
        if key in seen:
            raise ValueError("duplicate PHPBench parameter: %s" % key)
        seen.add(key)
        values[key] = value

    raw_iterations = values["iterations"]
    if not re.fullmatch(r"[0-9]+", raw_iterations):
        raise ValueError("iterations must be a base-10 integer")
    iterations = int(raw_iterations)
    if not MIN_ITERATIONS <= iterations <= MAX_ITERATIONS:
        raise ValueError(
            "iterations must be between %d and %d" % (MIN_ITERATIONS, MAX_ITERATIONS)
        )
    values["iterations"] = iterations
    return values


def expected_runtime_seconds(iterations):
    """Estimate one sample's workload time from the full-profile native probe."""
    if type(iterations) is not int or not MIN_ITERATIONS <= iterations <= MAX_ITERATIONS:
        raise ValueError("iterations are outside the supported PHPBench range")
    return max(
        1,
        math.ceil(BASELINE_RUNTIME_SECONDS * iterations / BASELINE_ITERATIONS),
    )


def load_build_manifest(base_environment=None, deadline_monotonic=None):
    """Verify the installed source tree and Workshop provenance before execution."""
    environment = sanitized_php_environment(base_environment)

    def check_deadline():
        if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
            raise TimeoutError("PHPBench preflight exceeded its work deadline")

    check_deadline()
    manifest = json.loads(BUILD_MANIFEST.read_text(encoding="utf-8"))
    if (
        manifest.get("format_version") != 2
        or manifest.get("benchmark") != BENCHMARK
        or manifest.get("status") != "ready"
    ):
        raise ValueError("PHPBench build manifest is not ready")
    source = manifest.get("source")
    if not isinstance(source, dict) or source != {
        "url": SOURCE_URL,
        "version": PHPBENCH_VERSION,
        "archive_sha256": SOURCE_SHA256,
        "archive_md5": SOURCE_MD5,
        "archive_bytes": SOURCE_BYTES,
    }:
        raise ValueError("PHPBench build manifest does not match the pinned upstream source")
    profile = manifest.get("profile_compatibility")
    if not isinstance(profile, dict) or profile != {
        "profile_id": PROFILE_ID,
        "profile_version": PROFILE_VERSION,
        "patched_archive_sha256": PROFILE_PATCHED_ARCHIVE_SHA256,
        "patch_sha256": COMPAT_PATCH_SHA256,
    }:
        raise ValueError("PHPBench compatibility patch provenance is incomplete")
    architecture = manifest.get("architecture")
    if architecture not in SUPPORTED_ARCHITECTURES or architecture != platform.machine():
        raise ValueError("PHPBench Workshop build architecture does not match this engine")
    if manifest.get("install_root") != str(INSTALL_ROOT):
        raise ValueError("PHPBench was installed at an unexpected path")

    test_names = manifest.get("test_names")
    if not isinstance(test_names, list) or test_names != list(EXPECTED_TEST_NAMES):
        raise ValueError("PHPBench build manifest has an invalid native test list")

    expected_files = manifest.get("file_sha256")
    if not isinstance(expected_files, dict) or not expected_files:
        raise ValueError("PHPBench build manifest has no installed source file hashes")
    actual_files = {}
    for path in sorted(INSTALL_ROOT.rglob("*")):
        check_deadline()
        if path.is_file() and path.name != "build-manifest.json":
            actual_files[path.relative_to(INSTALL_ROOT).as_posix()] = sha256_file(
                path, deadline_monotonic
            )
    if actual_files != expected_files:
        raise ValueError("Installed PHPBench source files differ from Workshop provenance")

    php = manifest.get("php")
    if not isinstance(php, dict) or not isinstance(php.get("binary"), str):
        raise ValueError("PHPBench build manifest has no PHP CLI provenance")
    php_binary = Path(php["binary"])
    check_deadline()
    if not php_binary.is_file() or sha256_file(php_binary, deadline_monotonic) != php.get(
        "binary_sha256"
    ):
        raise ValueError("PHP CLI binary does not match Workshop provenance")
    version_result = subprocess.run(
        [str(php_binary), "--version"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=environment,
        timeout=_command_timeout(deadline_monotonic),
        check=False,
    )
    version_match = re.search(r"^PHP\s+([^\s]+)", version_result.stdout, re.MULTILINE)
    if (
        version_result.returncode != 0
        or not version_match
        or version_match.group(1) != php.get("version")
    ):
        raise ValueError("PHP CLI version differs from Workshop provenance")

    runtime_configuration = php.get("runtime_configuration")
    configuration_hash = php.get("runtime_configuration_sha256")
    verify_php_runtime_configuration(
        runtime_configuration, runtime_configuration, configuration_hash
    )
    actual_configuration = capture_php_runtime_configuration(
        php_binary, environment, deadline_monotonic
    )
    verify_php_runtime_configuration(
        runtime_configuration, actual_configuration, configuration_hash
    )

    packages = php.get("rpm_packages")
    verify_php_package_provenance(
        packages, packages, php.get("rpm_packages_sha256")
    )
    actual_packages = php_package_provenance(environment, deadline_monotonic)
    verify_php_package_provenance(
        packages, actual_packages, php.get("rpm_packages_sha256")
    )
    return manifest


def parse_native_output(output_text, iterations, test_names):
    """Validate the complete suite output and return the native score and evidence."""
    if not isinstance(output_text, str):
        raise ValueError("PHPBench stdout is not text")
    if not isinstance(test_names, list) or len(test_names) != EXPECTED_TEST_COUNT:
        raise ValueError("Expected PHPBench test list is invalid")

    version_matches = re.findall(r"^PHPBench\s*:\s*(\S+)\s*$", output_text, re.MULTILINE)
    php_version_matches = re.findall(r"^PHP version:\s*(\S+)\s*$", output_text, re.MULTILINE)
    tests_matches = re.findall(r"^Tests\s*:\s*([0-9]+)\s*$", output_text, re.MULTILINE)
    iterations_matches = re.findall(
        r"^Iterations\s*:\s*([0-9]+)\s*$", output_text, re.MULTILINE
    )
    total_time_matches = re.findall(
        r"^Total time\s*:\s*([0-9]+) seconds\s*$", output_text, re.MULTILINE
    )
    score_matches = re.findall(
        r"^Score\s*:\s*([0-9]+) \(higher is better\)\s*$",
        output_text,
        re.MULTILINE,
    )
    if (
        version_matches != [PHPBENCH_VERSION]
        or len(php_version_matches) != 1
        or tests_matches != [str(EXPECTED_TEST_COUNT)]
        or iterations_matches != [str(iterations)]
        or len(total_time_matches) != 1
        or len(score_matches) != 1
    ):
        raise ValueError("PHPBench output is missing or disagrees with its final summary")

    records = []
    for line in output_text.splitlines():
        match = TEST_ROW_RE.fullmatch(line)
        if match:
            duration = float(match.group(2))
            if not math.isfinite(duration) or duration < 0:
                raise ValueError("PHPBench emitted an invalid test duration")
            records.append({"test": match.group(1), "seconds": duration})
    actual_names = [record["test"] for record in records]
    if actual_names != test_names:
        raise ValueError(
            "PHPBench measured test rows are missing, duplicated, or out of order"
        )

    score = int(score_matches[0])
    if score <= 0:
        raise ValueError("PHPBench emitted a nonpositive aggregate score")
    return {
        "version": PHPBENCH_VERSION,
        "php_version": php_version_matches[0],
        "test_count": EXPECTED_TEST_COUNT,
        "test_names": actual_names,
        "test_durations": records,
        "iterations": iterations,
        "total_time_rounded_seconds": int(total_time_matches[0]),
        "score": score,
        "direction": "higher-is-better",
    }
