"""Standard-library, read-only SHA verification, including the raw archive."""
import argparse
import hashlib
import json
import tarfile
from pathlib import Path


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def verify(package, verify_raw=False):
    package = Path(package).resolve()
    manifest = json.loads((package / "sha256_manifest.json").read_text())
    issues = []
    for rel, expected in manifest["files"].items():
        path = (package / rel).resolve()
        if not path.is_relative_to(package) or not path.is_file() or sha(path) != expected:
            issues.append({"kind": "package_file_sha", "path": rel})
    request = json.loads((package / "APPROVAL_REQUEST.json").read_text())
    if sha(package / "sha256_manifest.json") != request["candidate_manifest_sha256"]:
        issues.append({"kind": "approval_candidate_identity"})
    raw_count = 0
    if verify_raw:
        raw_manifest = json.loads((package / "frozen/raw_manifest.json").read_text())
        found = set()
        with tarfile.open(package / "frozen/raw_development_evidence.tar.gz", "r:gz") as archive:
            for member in archive:
                if not member.isfile():
                    if not member.isdir():
                        issues.append({"kind": "unexpected_archive_member", "path": member.name})
                    continue
                rel = member.name
                if rel not in raw_manifest["files"] or rel in found:
                    issues.append({"kind": "unlisted_or_duplicate_raw_file", "path": rel})
                    continue
                f = archive.extractfile(member)
                h = hashlib.sha256()
                for chunk in iter(lambda: f.read(1024 * 1024), b""):
                    h.update(chunk)
                if h.hexdigest() != raw_manifest["files"][rel]:
                    issues.append({"kind": "raw_file_sha", "path": rel})
                found.add(rel)
                raw_count += 1
        if found != set(raw_manifest["files"]):
            issues.append({"kind": "missing_raw_files", "paths": sorted(set(raw_manifest["files"]) - found)})
    return {"schema": "attentionbench.guidance-adoption-frozen-verification.v1.1",
            "pass": not issues, "frozen_files": len(manifest["files"]),
            "raw_files_verified": raw_count, "issues": issues,
            "candidate_manifest_sha256": sha(package / "sha256_manifest.json")}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    parser.add_argument("--verify-raw", action="store_true")
    args = parser.parse_args()
    result = verify(args.package, args.verify_raw)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["pass"] else 1)
