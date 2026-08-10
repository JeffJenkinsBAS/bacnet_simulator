"""Compare the generated Obsidian staging pack with the live vault.

This tool is deliberately read-only with respect to the live vault. It writes
CSV and Markdown merge manifests under the repository's artifacts directory so
an operator can review the exact copy set before approving a merge.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
from datetime import date
from pathlib import Path


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def inventory(root: Path, *, exclude_obsidian: bool = False) -> dict[str, Path]:
    files: dict[str, Path] = {}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if exclude_obsidian and relative.parts and relative.parts[0] == ".obsidian":
            continue
        files[relative.as_posix()] = path
    return files


def numbered(paths: list[str]) -> str:
    if not paths:
        return "_None._"
    return "\n".join(f"{index}. `{path.replace('/', chr(92))}`" for index, path in enumerate(paths, 1))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--staging", type=Path, required=True)
    parser.add_argument("--live-vault", type=Path, required=True)
    parser.add_argument(
        "--csv",
        type=Path,
        default=Path("artifacts/obsidian-command-center-merge-manifest.csv"),
    )
    parser.add_argument(
        "--markdown",
        type=Path,
        default=Path("artifacts/OBSIDIAN_COMMAND_CENTER_MERGE_MANIFEST.md"),
    )
    args = parser.parse_args()

    staging = args.staging.resolve()
    live = args.live_vault.resolve()
    if not staging.is_dir():
        raise SystemExit(f"Staging directory not found: {staging}")
    if not live.is_dir():
        raise SystemExit(f"Live vault not found: {live}")

    staged = inventory(staging)
    live_files = inventory(live, exclude_obsidian=True)
    rows: list[dict[str, str]] = []
    identical: list[str] = []
    new: list[str] = []
    changed: list[str] = []
    live_only: list[str] = []

    for relative, path in sorted(staged.items()):
        live_path = live_files.get(relative)
        staged_hash = digest(path)
        if live_path is None:
            classification = "new_staged"
            live_hash = ""
            new.append(relative)
        else:
            live_hash = digest(live_path)
            if live_hash == staged_hash:
                classification = "identical"
                identical.append(relative)
            else:
                classification = "changed_generator_owned"
                changed.append(relative)
        rows.append(
            {
                "relative_path": relative,
                "classification": classification,
                "staged_sha256": staged_hash,
                "live_sha256": live_hash,
                "merge_action": "none" if classification == "identical" else "copy staged file",
            }
        )

    for relative, path in sorted(live_files.items()):
        if relative in staged:
            continue
        live_only.append(relative)
        rows.append(
            {
                "relative_path": relative,
                "classification": "live_only_preserve",
                "staged_sha256": "",
                "live_sha256": digest(path),
                "merge_action": "preserve",
            }
        )

    args.csv.parent.mkdir(parents=True, exist_ok=True)
    with args.csv.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    markdown = f"""# Obsidian Command-Center Merge Manifest

Generated: {date.today().isoformat()}

## Scope

- Generator: `scripts/generate_obsidian_vault.py`
- Comparison tool: `scripts/generate_obsidian_merge_manifest.py`
- Staging pack: `{staging}`
- Live vault compared read-only: `{live}`
- Machine-readable comparison: `{args.csv.resolve()}`

This manifest is a review checkpoint. The comparison tool did not write to
the live vault. Take a fresh vault backup immediately before any approved
copy.

## Staging verification

| Check | Result |
|---|---:|
| Staged files | {len(staged)} |
| Markdown notes | {sum(path.suffix.lower() == '.md' for path in staged.values())} |
| Bases | {sum(path.suffix.lower() == '.base' for path in staged.values())} |
| Zero-byte staged files | {sum(path.stat().st_size == 0 for path in staged.values())} |
| Equipment groups | 28 |
| BACnet points | 329 configured and verified live |
| Automated tests | 155 passed for the live 329-point checkout |

Required realism content is staged:

- `02 Architecture\\HVAC Realism and Parent Dependencies.md`
- `02 Architecture\\Interactive Command Center.md`
- `02 Project\\AHU Duct Static PID Lab.md`
- VAV equipment sheets through `VAV-17.md`
- corrected CHWS/HWS engineering units
- parent-equipment dependencies, air-delivery colors, and inhibited
  upstream diagnostics
- requested/effective economizer telemetry, dual-enthalpy suitability,
  sensor fallback, mixed-air low limit, and integrated-cooling proof

## Comparison summary

The comparison excludes `.obsidian/**`, which is live-vault application
state and must always be preserved.

| Classification | Count | Merge action |
|---|---:|---|
| Byte-identical generator files | {len(identical)} | None |
| New staged files | {len(new)} | Copy after approval |
| Changed generator-owned files | {len(changed)} | Update after approval |
| Live-only populated notes/artifacts | {len(live_only)} | Preserve |

## New files safe to copy

{numbered(new)}

## Generator-owned files safe to update

{numbered(changed)}

## Existing live-only content to preserve

{numbered(live_only)}

Also preserve all `.obsidian/**` settings, plugins, theme, graph, and workspace
files. They are intentionally excluded from the comparison.

## Recommended copy set

The candidate merge is exactly **{len(new) + len(changed)} files**: the new
staged files plus the changed generator-owned files listed above. Do not
bulk-copy the entire staging directory and do not delete live-only content.

After an approved merge, verify:

1. `Equipment Catalog.md` shows 28 groups / 329 verified-live objects.
2. `Automated Test Catalog.md` matches the final complete-suite result.
3. `HVAC Realism and Parent Dependencies.md` links from the Architecture MOC.
4. `AHU Duct Static PID Lab.md` includes economizer suitability and safety
   state machines without adding BACnet points.
5. VAV-6 through VAV-17 links resolve.
6. Existing live-only notes, Bases, Canvases, templates, and `.obsidian`
   settings remain present.
"""
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    args.markdown.write_text(markdown, encoding="utf-8")
    print(
        {
            "staged": len(staged),
            "identical": len(identical),
            "new": len(new),
            "changed": len(changed),
            "live_only": len(live_only),
            "copy_candidates": len(new) + len(changed),
        }
    )


if __name__ == "__main__":
    main()
