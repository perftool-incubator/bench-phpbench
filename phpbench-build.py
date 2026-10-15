#!/usr/bin/env python3
# -*- mode: python; indent-tabs-mode: nil; python-indent-level: 4 -*-
# vim: autoindent tabstop=4 shiftwidth=4 expandtab softtabstop=4 filetype=python

"""Verify and install PHPBench for a prepared Crucible userenv image."""

import argparse
import hashlib
import io
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

from phpbench_common import (
    BENCHMARK,
    COMPAT_PATCH_SHA256,
    EXPECTED_TEST_COUNT,
    EXPECTED_TEST_NAMES,
    INSTALL_ROOT,
    PHPBENCH_VERSION,
    PROFILE_ID,
    PROFILE_PATCHED_ARCHIVE_SHA256,
    PROFILE_VERSION,
    SOURCE_BYTES,
    SOURCE_MD5,
    SOURCE_SHA256,
    SOURCE_URL,
    SUPPORTED_ARCHITECTURES,
    canonical_json_sha256,
    capture_php_runtime_configuration,
    php_package_provenance,
    sanitized_php_environment,
    sha256_file,
)

SOURCE_ARCHIVE_ROOT = "phpbench-0.8.1"
MAX_UNPACKED_ARCHIVE_BYTES = 2 * 1024 * 1024


def php_binary_provenance():
    php = shutil.which("php")
    if not php:
        raise RuntimeError("Workshop did not provide the PHP CLI binary")
    binary = Path(php).resolve()
    environment = sanitized_php_environment()
    version_result = subprocess.run(
        [str(binary), "--version"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=environment,
        timeout=30,
        check=False,
    )
    if version_result.returncode != 0:
        raise RuntimeError("PHP CLI --version failed")
    version_line = version_result.stdout.splitlines()[0]
    version_parts = version_line.split()
    if len(version_parts) < 2 or version_parts[0] != "PHP":
        raise RuntimeError("Could not parse PHP CLI version output")

    packages = php_package_provenance(environment)
    php_cli_packages = [item["nevra"] for item in packages if item["name"] == "php-cli"]
    if len(php_cli_packages) != 1:
        raise RuntimeError("Could not identify exactly one installed php-cli RPM")
    runtime_configuration = capture_php_runtime_configuration(binary, environment)
    return {
        "binary": str(binary),
        "binary_sha256": sha256_file(binary),
        "version": version_parts[1],
        "version_output": version_line,
        "rpm_nevra": php_cli_packages[0],
        "rpm_packages": packages,
        "rpm_packages_sha256": canonical_json_sha256(packages),
        "runtime_configuration": runtime_configuration,
        "runtime_configuration_sha256": canonical_json_sha256(runtime_configuration),
    }


def php_lint(binary, source_root, environment):
    php_files = sorted(source_root.rglob("*.php"))
    if len(php_files) != EXPECTED_TEST_COUNT + 1:
        raise RuntimeError("PHPBench source does not contain the expected PHP file count")
    for path in php_files:
        result = subprocess.run(
            [binary, "-l", str(path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
            timeout=30,
            check=False,
        )
        if result.returncode != 0 or "No syntax errors detected" not in result.stdout:
            raise RuntimeError("PHP syntax check failed for %s: %s" % (path, result.stdout))


def _archive_member_path(member):
    name = member.name
    if (
        not name
        or "\x00" in name
        or "\\" in name
        or name.startswith("/")
    ):
        raise RuntimeError("PHPBench archive contains an unsafe path")

    normalized = name[:-1] if name.endswith("/") else name
    parts = normalized.split("/")
    if not normalized or any(part in ("", ".", "..") for part in parts):
        raise RuntimeError("PHPBench archive contains an unsafe path")
    if parts[0] != SOURCE_ARCHIVE_ROOT:
        raise RuntimeError("PHPBench archive contains an unexpected top-level path")
    return "/".join(parts[1:])


def _pinned_archive_entries(archive):
    """Validate the archive digest and every member before returning file data."""
    archive = Path(archive)
    if archive.name != "phpbench-0.8.1.tar.gz":
        raise RuntimeError("PHPBench archive filename is unexpected")

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(str(archive), flags)
    except OSError as exc:
        raise RuntimeError("Could not open the PHPBench source archive safely") from exc
    with os.fdopen(descriptor, "rb") as stream:
        archive_stat = os.fstat(stream.fileno())
        if not stat.S_ISREG(archive_stat.st_mode):
            raise RuntimeError("PHPBench source archive is not a regular file")
        if archive_stat.st_size != SOURCE_BYTES:
            raise RuntimeError("PHPBench source archive size does not match the upstream release")
        archive_data = stream.read(SOURCE_BYTES + 1)

    if len(archive_data) != SOURCE_BYTES:
        raise RuntimeError("PHPBench source archive size changed while it was read")
    if (
        hashlib.sha256(archive_data).hexdigest() != SOURCE_SHA256
        or hashlib.md5(archive_data).hexdigest() != SOURCE_MD5
    ):
        raise RuntimeError("PHPBench source archive does not match the upstream release")

    try:
        source_archive = tarfile.open(fileobj=io.BytesIO(archive_data), mode="r:gz")
        with source_archive:
            members = source_archive.getmembers()
            entries = []
            seen_paths = {}
            total_unpacked_bytes = 0
            root_mode = 0o755

            for member in members:
                relative = _archive_member_path(member)
                is_root = relative == ""
                if member.type == tarfile.DIRTYPE:
                    if member.size != 0:
                        raise RuntimeError("PHPBench archive directory has unexpected data")
                    if member.mode & 0o7000:
                        raise RuntimeError("PHPBench archive contains unsafe permission bits")
                    kind = "directory"
                    mode = member.mode & 0o777
                    if is_root:
                        root_mode = mode
                elif member.type in (tarfile.REGTYPE, tarfile.AREGTYPE):
                    if is_root or member.name.endswith("/"):
                        raise RuntimeError("PHPBench archive contains an unexpected file path")
                    if member.mode & 0o7000:
                        raise RuntimeError("PHPBench archive contains unsafe permission bits")
                    if member.size < 0:
                        raise RuntimeError("PHPBench archive contains an invalid file size")
                    total_unpacked_bytes += member.size
                    if total_unpacked_bytes > MAX_UNPACKED_ARCHIVE_BYTES:
                        raise RuntimeError("PHPBench archive exceeds the unpacked size limit")
                    data_stream = source_archive.extractfile(member)
                    if data_stream is None:
                        raise RuntimeError("Could not read a PHPBench archive member")
                    content = bytearray()
                    while True:
                        block = data_stream.read(64 * 1024)
                        if not block:
                            break
                        content.extend(block)
                        if len(content) > member.size:
                            raise RuntimeError("PHPBench archive member exceeds its declared size")
                    if len(content) != member.size:
                        raise RuntimeError("PHPBench archive member size does not match its header")
                    content = bytes(content)
                    kind = "file"
                    mode = member.mode & 0o777
                else:
                    raise RuntimeError(
                        "PHPBench archive contains a link or unsupported file type"
                    )

                if relative in seen_paths:
                    raise RuntimeError("PHPBench archive contains a duplicate member path")
                seen_paths[relative] = kind
                entry = {"kind": kind, "path": relative, "mode": mode}
                if kind == "file":
                    entry.update(
                        {
                            "data": content,
                            "size": len(content),
                            "sha256": hashlib.sha256(content).hexdigest(),
                        }
                    )
                entries.append(entry)

            if not any(entry["kind"] == "file" for entry in entries):
                raise RuntimeError("PHPBench archive contains no source files")

            file_paths = {
                entry["path"] for entry in entries if entry["kind"] == "file"
            }
            for entry in entries:
                parts = entry["path"].split("/") if entry["path"] else []
                for parent_index in range(1, len(parts)):
                    parent_path = "/".join(parts[:parent_index])
                    if parent_path in file_paths:
                        raise RuntimeError("PHPBench archive path conflicts with a file")
            return entries, root_mode
    except (OSError, EOFError, tarfile.TarError) as exc:
        raise RuntimeError("PHPBench source archive is not a valid gzip tar archive") from exc


def _expected_source_inventory(entries, root_mode):
    expected_files = {}
    expected_directories = {"": root_mode}
    for entry in entries:
        path = entry["path"]
        if entry["kind"] == "directory":
            expected_directories[path] = entry["mode"]
        else:
            expected_files[path] = {
                "mode": entry["mode"],
                "size": entry["size"],
                "sha256": entry["sha256"],
            }
        parts = path.split("/") if path else []
        for parent_index in range(1, len(parts)):
            parent_path = "/".join(parts[:parent_index])
            expected_directories.setdefault(parent_path, 0o755)
    return expected_files, expected_directories


def verify_unpacked_source(entries, source_dir, root_mode=0o755):
    expected_files, expected_directories = _expected_source_inventory(entries, root_mode)
    source_dir = Path(source_dir)
    try:
        source_stat = source_dir.lstat()
    except OSError as exc:
        raise RuntimeError("Workshop did not unpack the expected PHPBench source directory") from exc
    if not stat.S_ISDIR(source_stat.st_mode):
        raise RuntimeError("PHPBench unpacked source root is not a real directory")

    actual_files = {}
    actual_directories = {"": stat.S_IMODE(source_stat.st_mode)}
    for path in sorted(source_dir.rglob("*")):
        relative = path.relative_to(source_dir).as_posix()
        mode = stat.S_IMODE(path.lstat().st_mode)
        file_stat = path.lstat()
        if stat.S_ISDIR(file_stat.st_mode):
            actual_directories[relative] = mode
        elif stat.S_ISREG(file_stat.st_mode):
            actual_files[relative] = {
                "mode": mode,
                "size": file_stat.st_size,
                "sha256": sha256_file(path),
            }
        else:
            raise RuntimeError("PHPBench unpacked source contains a link or special file")

    if actual_files != expected_files or actual_directories != expected_directories:
        raise RuntimeError(
            "Workshop-unpacked files differ from the pinned PHPBench archive inventory"
        )


def _extract_validated_entries(entries, source_dir, root_mode):
    _, expected_directories = _expected_source_inventory(entries, root_mode)
    os.chmod(source_dir, 0o700)
    for relative in sorted(expected_directories, key=lambda value: (value.count("/"), value)):
        if relative:
            (source_dir / relative).mkdir(mode=0o700, parents=True, exist_ok=True)

    for entry in entries:
        if entry["kind"] != "file":
            continue
        destination = source_dir / entry["path"]
        with destination.open("xb") as stream:
            stream.write(entry["data"])
        os.chmod(destination, entry["mode"])

    for relative, mode in sorted(
        expected_directories.items(),
        key=lambda item: (item[0].count("/"), item[0]),
        reverse=True,
    ):
        destination = source_dir if not relative else source_dir / relative
        os.chmod(destination, mode)


def safe_unpack_archive(archive, source_dir):
    """Verify the archive before extracting any bytes with Workshop running as root."""
    archive = Path(archive)
    source_dir = Path(source_dir)
    if source_dir.name != SOURCE_ARCHIVE_ROOT:
        raise RuntimeError("PHPBench source directory name is unexpected")
    entries, root_mode = _pinned_archive_entries(archive)
    if source_dir.exists() or source_dir.is_symlink():
        raise RuntimeError("PHPBench source directory already exists before safe extraction")

    source_dir.parent.mkdir(parents=True, exist_ok=True)
    staging_dir = Path(
        tempfile.mkdtemp(prefix=".phpbench-0.8.1-extract-", dir=str(source_dir.parent))
    )
    try:
        _extract_validated_entries(entries, staging_dir, root_mode)
        verify_unpacked_source(entries, staging_dir, root_mode)
        os.replace(staging_dir, source_dir)
    except Exception:
        shutil.rmtree(staging_dir)
        raise


def _validate_archive_only(archive):
    """Validate archive and member records without extracting them."""
    return _pinned_archive_entries(archive)


def atomic_json(path, value):
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def build(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--patch", type=Path)
    parser.add_argument(
        "--safe-unpack",
        action="store_true",
        help="validate the pinned archive and safely extract it before Workshop's build stage",
    )
    args = parser.parse_args(argv)

    archive = args.archive.absolute()
    source_dir = args.source_dir.absolute()
    if args.safe_unpack:
        if args.patch is not None:
            parser.error("--patch must not be supplied with --safe-unpack")
        safe_unpack_archive(archive, source_dir)
        print("Validated and safely extracted the pinned PHPBench source archive")
        return
    if args.patch is None:
        parser.error("--patch is required for a PHPBench build")

    patch_file = args.patch.absolute()
    if source_dir.name != SOURCE_ARCHIVE_ROOT or not source_dir.is_dir() or source_dir.is_symlink():
        raise RuntimeError("Workshop did not unpack the expected PHPBench source directory")
    archive_entries, root_mode = _validate_archive_only(archive)
    verify_unpacked_source(archive_entries, source_dir, root_mode)
    if sha256_file(patch_file) != COMPAT_PATCH_SHA256:
        raise RuntimeError("PHPBench compatibility patch does not match its source review")

    architecture = platform.machine()
    if architecture not in SUPPORTED_ARCHITECTURES:
        raise RuntimeError("Unsupported PHPBench architecture: %s" % architecture)

    if INSTALL_ROOT.exists():
        shutil.rmtree(INSTALL_ROOT)
    INSTALL_ROOT.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_dir, INSTALL_ROOT)

    patch_result = subprocess.run(
        ["patch", "--batch", "--forward", "--fuzz=0", "-p1", "-i", str(patch_file)],
        cwd=INSTALL_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if patch_result.returncode != 0 or "FAILED" in patch_result.stdout:
        raise RuntimeError("Could not apply the reviewed PHP compatibility patch: %s" % patch_result.stdout)

    test_names = sorted(path.stem for path in (INSTALL_ROOT / "tests").glob("test_*.php"))
    if test_names != list(EXPECTED_TEST_NAMES):
        raise RuntimeError("PHPBench source test list differs from the pinned native suite")
    if len(list(INSTALL_ROOT.rglob("*"))) < EXPECTED_TEST_COUNT:
        raise RuntimeError("PHPBench installation is incomplete")

    php = php_binary_provenance()
    php_lint(php["binary"], INSTALL_ROOT, sanitized_php_environment())
    file_sha256 = {
        path.relative_to(INSTALL_ROOT).as_posix(): sha256_file(path)
        for path in sorted(INSTALL_ROOT.rglob("*"))
        if path.is_file()
    }
    manifest = {
        "format_version": 2,
        "benchmark": BENCHMARK,
        "status": "ready",
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
        "architecture": architecture,
        "install_root": str(INSTALL_ROOT),
        "php": php,
        "test_count": EXPECTED_TEST_COUNT,
        "test_names": test_names,
        "file_sha256": file_sha256,
    }
    atomic_json(INSTALL_ROOT / "build-manifest.json", manifest)
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    try:
        build(sys.argv[1:])
    except Exception as exc:
        print("PHPBench Workshop build failed: %s" % exc, file=sys.stderr)
        sys.exit(1)
