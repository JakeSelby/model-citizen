#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build and compare canonical manifests for the Studio browser bundle."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath


SCHEMA_VERSION = 1
NORMALIZED_ENV = {"TZ": "UTC", "LANG": "C", "LC_ALL": "C", "CI": "1"}
EXCLUDED_SOURCE_PARTS = {"node_modules", "dist", ".npm-cache", "reproducibility"}
QUALIFYING_LICENSES = {
    "(MIT OR CC0-1.0)",
    "0BSD",
    "Apache-2.0",
    "BSD-3-Clause",
    "CC-BY-4.0",
    "ISC",
    "MIT",
    "MIT AND ISC",
}
class UnsafeOutput(ValueError):
    """Raised when output or manifest paths can leave their declared root."""


def _safe_relative(value):
    path = PurePosixPath(value)
    if path.is_absolute() or not value or any(part in ("", ".", "..") for part in path.parts):
        raise UnsafeOutput("unsafe relative path: {}".format(value))
    return path


def _inside(root, candidate):
    try:
        candidate.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_manifest(root):
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise UnsafeOutput("bundle root must be a real directory: {}".format(root))
    files = []
    for current, directories, names in os.walk(str(root), followlinks=False):
        current_path = Path(current)
        for directory in directories:
            target = current_path / directory
            if target.is_symlink() or not _inside(root, target):
                raise UnsafeOutput("unsafe directory in bundle: {}".format(target))
        for name in names:
            target = current_path / name
            if target.is_symlink() or not target.is_file() or not _inside(root, target):
                raise UnsafeOutput("unsafe file in bundle: {}".format(target))
            relative = target.relative_to(root).as_posix()
            _safe_relative(relative)
            files.append({"path": relative, "sha256": sha256_file(target), "size": target.stat().st_size})
    files.sort(key=lambda item: item["path"])
    return {"schema_version": SCHEMA_VERSION, "files": files}


def load_manifest(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema_version") != SCHEMA_VERSION or not isinstance(data.get("files"), list):
        raise ValueError("unsupported manifest: {}".format(path))
    result = {}
    for item in data["files"]:
        relative = _safe_relative(item.get("path", "")).as_posix()
        if relative in result:
            raise ValueError("duplicate manifest path: {}".format(relative))
        digest = item.get("sha256")
        size = item.get("size")
        if not isinstance(digest, str) or len(digest) != 64 or not isinstance(size, int):
            raise ValueError("invalid manifest entry: {}".format(relative))
        result[relative] = {"sha256": digest, "size": size}
    return result


def compare_manifests(paths):
    if len(paths) < 2:
        raise ValueError("at least two manifests are required")
    loaded = [load_manifest(path) for path in paths]
    reference = loaded[0]
    comparisons = []
    identical = True
    for path, candidate in zip(paths[1:], loaded[1:]):
        missing = sorted(set(reference) - set(candidate))
        extra = sorted(set(candidate) - set(reference))
        differing = sorted(
            name for name in set(reference) & set(candidate) if reference[name] != candidate[name]
        )
        if missing or extra or differing:
            identical = False
        comparisons.append({
            "candidate": Path(path).name,
            "missing": missing,
            "extra": extra,
            "differing": differing,
        })
    return {
        "schema_version": SCHEMA_VERSION,
        "reference": Path(paths[0]).name,
        "identical": identical,
        "comparisons": comparisons,
    }


def compare_manifest_groups(references, candidates):
    if not references or not candidates:
        raise ValueError("reference and candidate manifests are required")
    comparisons = []
    identical = True
    for reference_path in references:
        reference = load_manifest(reference_path)
        for candidate_path in candidates:
            candidate = load_manifest(candidate_path)
            missing = sorted(set(reference) - set(candidate))
            extra = sorted(set(candidate) - set(reference))
            differing = sorted(
                name for name in set(reference) & set(candidate) if reference[name] != candidate[name]
            )
            if missing or extra or differing:
                identical = False
            comparisons.append({
                "reference": Path(reference_path).as_posix(),
                "candidate": Path(candidate_path).as_posix(),
                "missing": missing,
                "extra": extra,
                "differing": differing,
            })
    return {
        "schema_version": SCHEMA_VERSION,
        "identical": identical,
        "comparisons": comparisons,
    }


def write_json(path, data):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def expected_runtime(source):
    source = Path(source).resolve()
    node_pin = source.parent / ".node-version"
    if not node_pin.is_file() or node_pin.is_symlink():
        raise ValueError("missing safe Node version pin: {}".format(node_pin))
    package_path = source / "package.json"
    if not package_path.is_file() or package_path.is_symlink():
        raise ValueError("missing safe package metadata: {}".format(package_path))
    package = json.loads(package_path.read_text(encoding="utf-8"))
    manager = package.get("packageManager", "")
    if not manager.startswith("npm@") or not manager[4:]:
        raise ValueError("packageManager must pin npm exactly")
    return {"node": node_pin.read_text(encoding="utf-8").strip(), "npm": manager[4:]}


def runtime_metadata(cwd, env=None):
    node = subprocess.run(
        ["node", "-p", "JSON.stringify({node:process.versions.node,os:process.platform,arch:process.arch})"],
        cwd=str(cwd), env=env, check=True, capture_output=True, text=True,
    )
    metadata = json.loads(node.stdout)
    npm = subprocess.run(
        ["npm", "--version"], cwd=str(cwd), env=env, check=True, capture_output=True, text=True,
    )
    metadata["npm"] = npm.stdout.strip()
    return metadata


def enforce_runtime(expected, actual):
    for name in ("node", "npm"):
        if actual.get(name) != expected.get(name):
            raise ValueError(
                "{} version mismatch: expected {}, found {}".format(
                    name, expected.get(name), actual.get(name)
                )
            )


def runtime_evidence(actual):
    evidence = dict(actual)
    evidence["npm"] = actual["npm"].split(".")
    return evidence


def _package_location(location):
    relative = _safe_relative(location)
    if not relative.parts or relative.parts[0] != "node_modules":
        raise UnsafeOutput("unsafe package location: {}".format(location))
    return relative


def _package_root(lock_root, location):
    lock_root = Path(lock_root).resolve()
    relative = _package_location(location)
    candidate = lock_root.joinpath(*relative.parts)
    current = lock_root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise UnsafeOutput("symlinked package location: {}".format(location))
    if not _inside(lock_root, candidate):
        raise UnsafeOutput("escaping package location: {}".format(location))
    return candidate


def _read_regular_file(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise UnsafeOutput("unsafe package metadata or license file: {}".format(path))
    return path.read_bytes()


def load_external_license_evidence(evidence_path):
    evidence_path = Path(evidence_path).resolve()
    if evidence_path.is_symlink() or not evidence_path.is_file():
        raise UnsafeOutput("missing safe external license evidence mapping: {}".format(evidence_path))
    data = json.loads(evidence_path.read_text(encoding="utf-8"))
    if data.get("schema_version") != SCHEMA_VERSION or not isinstance(data.get("packages"), list):
        raise ValueError("unsupported external license evidence mapping")
    records = {}
    for metadata in data["packages"]:
        required = (
            "name", "version", "license", "path", "source_commit", "source_url", "sha256", "basis"
        )
        if not all(isinstance(metadata.get(name), str) and metadata[name] for name in required):
            raise ValueError("incomplete external license evidence")
        package_key = (metadata["name"], metadata["version"])
        if package_key in records:
            raise ValueError("duplicate external license evidence: {} {}".format(*package_key))
        relative = _safe_relative(metadata["path"])
        license_path = evidence_path.parent.joinpath(*relative.parts)
        current = evidence_path.parent
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise UnsafeOutput("symlinked external license evidence: {}".format(package_key))
        if not _inside(evidence_path.parent, license_path):
            raise UnsafeOutput("escaping external license evidence: {}".format(package_key))
        raw = _read_regular_file(license_path)
        if hashlib.sha256(raw).hexdigest() != metadata["sha256"]:
            raise ValueError("external license evidence hash mismatch: {}".format(package_key))
        records[package_key] = dict(metadata, raw=raw)
    return records


def build_license_inventory(lock_path, evidence_path=None):
    lock_path = Path(lock_path)
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    external = load_external_license_evidence(evidence_path) if evidence_path else {}
    packages = []
    counts = {}
    for location, metadata in lock.get("packages", {}).items():
        if not location:
            continue
        _package_location(location)
        name = location.rsplit("node_modules/", 1)[-1]
        version = metadata.get("version")
        license_id = metadata.get("license")
        resolved = metadata.get("resolved")
        integrity = metadata.get("integrity")
        if not all(isinstance(value, str) and value for value in (name, version, license_id, resolved, integrity)):
            raise ValueError("incomplete dependency metadata: {}".format(location))
        if license_id not in QUALIFYING_LICENSES:
            raise ValueError("unreviewed or disallowed license {}: {}".format(license_id, location))
        counts[license_id] = counts.get(license_id, 0) + 1
        package = {
            "name": name,
            "version": version,
            "license": license_id,
            "artifact_url": resolved,
            "integrity": integrity,
            "scope": "development" if metadata.get("dev") else "runtime",
        }
        evidence = external.get((name, version))
        if evidence:
            package["external_license_evidence"] = {
                key: evidence[key]
                for key in ("basis", "license", "path", "sha256", "source_commit", "source_url")
            }
        packages.append(package)
    packages.sort(key=lambda item: (item["name"], item["version"], item["artifact_url"]))
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_from": lock_path.name,
        "package_count": len(packages),
        "license_counts": dict(sorted(counts.items())),
        "packages": packages,
    }


def build_runtime_notices(lock_path, evidence_path):
    lock_path = Path(lock_path).resolve()
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    external = load_external_license_evidence(evidence_path)
    used_external = set()
    groups = {}
    for location, metadata in lock.get("packages", {}).items():
        if not location or metadata.get("dev"):
            continue
        package_root = _package_root(lock_path.parent, location)
        candidates = []
        for path in package_root.iterdir():
            if not path.name.casefold().startswith(("license", "licence", "copying", "notice")):
                continue
            if path.is_symlink() or not path.is_file():
                raise UnsafeOutput("unsafe package license file: {}".format(path))
            candidates.append(path)
        candidates.sort()
        name = location.rsplit("node_modules/", 1)[-1]
        label = "{} {} ({})".format(name, metadata["version"], metadata["license"])
        if not candidates:
            package_key = (name, metadata["version"])
            evidence = external.get(package_key)
            if not evidence:
                raise ValueError(
                    "runtime dependency has no bundled or external license evidence: {} {}".format(
                        *package_key
                    )
                )
            if evidence["license"] != metadata["license"]:
                raise ValueError(
                    "external license evidence identifier mismatch: {} {}".format(*package_key)
                )
            raw = evidence["raw"]
            used_external.add(package_key)
            candidates = [None]
        for candidate in candidates:
            if candidate is not None:
                raw = _read_regular_file(candidate)
            digest = hashlib.sha256(raw).hexdigest()
            group = groups.setdefault(
                digest, {"components": [], "sources": [], "text": raw.decode("utf-8")}
            )
            group["components"].append(label)
            if candidate is None:
                group["sources"].append(
                    "{} @ {}".format(evidence["source_url"], evidence["source_commit"])
                )
            else:
                group["sources"].append("bundled {}".format(candidate.name))
    unused = sorted(set(external) - used_external)
    if unused:
        raise ValueError(
            "unused external license evidence: {}".format(
                ", ".join("{} {}".format(*item) for item in unused)
            )
        )
    notice = "\n".join([
        "Model Citizen Studio third-party notices",
        "",
        "This file is generated from the exact runtime dependency tree in package-lock.json.",
        "Each upstream license or notice body below is embedded byte for byte.",
    ]) + "\n"
    for digest, group in sorted(groups.items(), key=lambda item: sorted(item[1]["components"])):
        notice += "\n{}\nComponents: {}\nEvidence: {}\nLicense-file SHA-256: {}\n{}\n".format(
            "=" * 78,
            ", ".join(sorted(group["components"])),
            "; ".join(sorted(set(group["sources"]))),
            digest,
            "-" * 78,
        )
        notice += group["text"]
    return notice


def _archive_source(source, archive):
    source = Path(source).resolve()
    with tarfile.open(str(archive), "w") as bundle:
        for path in sorted(source.rglob("*"), key=lambda item: item.relative_to(source).as_posix()):
            relative = path.relative_to(source)
            if any(part in EXCLUDED_SOURCE_PARTS for part in relative.parts):
                continue
            if path.is_symlink():
                raise UnsafeOutput("source archive contains a symlink: {}".format(path))
            bundle.add(str(path), arcname=relative.as_posix(), recursive=False)


def _extract_source(archive, destination):
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=False)
    with tarfile.open(str(archive), "r") as bundle:
        for member in bundle.getmembers():
            relative = _safe_relative(member.name)
            target = destination.joinpath(*relative.parts)
            if not _inside(destination, target) or member.issym() or member.islnk():
                raise UnsafeOutput("unsafe source archive member: {}".format(member.name))
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                source = bundle.extractfile(member)
                if source is None:
                    raise UnsafeOutput("unreadable source archive member: {}".format(member.name))
                with target.open("wb") as handle:
                    shutil.copyfileobj(source, handle)
            else:
                raise UnsafeOutput("unsupported source archive member: {}".format(member.name))


def run_experiment(source, evidence, runs):
    source = Path(source).resolve()
    evidence = Path(evidence).resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    expected = expected_runtime(source)
    enforce_runtime(expected, runtime_metadata(source))
    manifests = []
    with tempfile.TemporaryDirectory(prefix="studio-repro-") as temp:
        temp_root = Path(temp)
        archive = temp_root / "source.tar"
        _archive_source(source, archive)
        for number in range(1, runs + 1):
            run_root = temp_root / "run-{}".format(number)
            extracted = run_root / "source"
            _extract_source(archive, extracted)
            cache = run_root / "npm-cache"
            cache.mkdir(parents=True)
            env = os.environ.copy()
            env.update(NORMALIZED_ENV)
            env["npm_config_cache"] = str(cache)
            runtime = runtime_metadata(extracted, env)
            enforce_runtime(expected, runtime)
            subprocess.run(["npm", "ci"], cwd=str(extracted), env=env, check=True)
            subprocess.run(["npm", "run", "build"], cwd=str(extracted), env=env, check=True)
            manifest_path = evidence / "run-{}.json".format(number)
            manifest = build_manifest(extracted / "dist")
            manifest["environment"] = runtime_evidence(runtime)
            write_json(manifest_path, manifest)
            manifests.append(manifest_path)
    report = compare_manifests(manifests)
    write_json(evidence / "comparison.json", report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create")
    create.add_argument("root")
    create.add_argument("output")
    compare = subparsers.add_parser("compare")
    compare.add_argument("manifests", nargs="+")
    compare.add_argument("--output", required=True)
    compare_groups = subparsers.add_parser("compare-groups")
    compare_groups.add_argument("--reference", nargs="+", required=True)
    compare_groups.add_argument("--candidate", nargs="+", required=True)
    compare_groups.add_argument("--output", required=True)
    experiment = subparsers.add_parser("experiment")
    experiment.add_argument("--source", required=True)
    experiment.add_argument("--evidence", required=True)
    experiment.add_argument("--runs", type=int, default=3)
    inventory = subparsers.add_parser("inventory")
    inventory.add_argument("--lock", required=True)
    inventory.add_argument("--license-evidence", required=True)
    inventory.add_argument("--output", required=True)
    inventory.add_argument("--notices-output", required=True)
    args = parser.parse_args(argv)

    if args.command == "create":
        write_json(args.output, build_manifest(args.root))
        return 0
    if args.command == "compare":
        report = compare_manifests(args.manifests)
        write_json(args.output, report)
        return 0 if report["identical"] else 1
    if args.command == "compare-groups":
        report = compare_manifest_groups(args.reference, args.candidate)
        write_json(args.output, report)
        return 0 if report["identical"] else 1
    if args.command == "inventory":
        write_json(args.output, build_license_inventory(args.lock, args.license_evidence))
        notices = Path(args.notices_output)
        notices.parent.mkdir(parents=True, exist_ok=True)
        notices.write_bytes(build_runtime_notices(args.lock, args.license_evidence).encode("utf-8"))
        return 0
    if args.runs < 2:
        parser.error("--runs must be at least 2")
    return 0 if run_experiment(args.source, args.evidence, args.runs)["identical"] else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, subprocess.CalledProcessError, tarfile.TarError) as error:
        print("studio bundle manifest: {}".format(error), file=sys.stderr)
        sys.exit(2)
