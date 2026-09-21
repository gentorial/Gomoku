"""Publish a completed, independently gated campaign model to GitHub Pages.

Without --publish this only records the decision and runtime metadata. Publication
checks the exact arena/model/reference hashes and refuses to replace a newer pin.
"""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
from gomoku_tools.protocol import ROOT
from .campaign import promotion_gate
from .io import file_sha256, read_json, write_json


def command(*args, capture=False):
    # Hidden Windows children need explicit output handles. Inheriting the
    # background campaign's handles can make Git fail without visible diagnostics.
    result = subprocess.run(list(map(str, args)), cwd=ROOT, text=True,
                            capture_output=True, encoding="utf-8",
                            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
    if not capture or result.returncode:
        if result.stdout:
            print(result.stdout, end="", flush=True)
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr, flush=True)
    result.check_returncode()
    return result


def decision(root):
    root = Path(root).resolve()
    status = read_json(root / "status.json")
    if status.get("stage") != "completed":
        raise ValueError("The campaign has not completed training, arena and runtime assessment")
    setup, selection = read_json(root / "campaign.json"), read_json(root / "selection.json")
    report, runtime = read_json(root / "promotion-arena.json"), read_json(root / "runtime.json")
    if (report["engines"]["a"]["model"]["sha256"] != selection["export"]["sha256"] or
            report["engines"]["b"]["model"]["sha256"] != setup["baselineModel"]["sha256"] or
            report["openings"]["sha256"] != file_sha256(root / "promotion-openings.json")):
        raise ValueError("Promotion arena is not tied to the selected candidate and frozen openings")
    if (not runtime.get("passed") or runtime["modelSha256"] != selection["export"]["sha256"] or
            runtime["referenceSha256"] != file_sha256(root / "assessment/reference-vectors.json")):
        raise ValueError("Runtime parity is missing or belongs to another candidate")
    for item in (selection["checkpoint"], selection["export"]):
        if file_sha256(item["path"]) != item["sha256"]:
            raise ValueError("Selected artifact changed after evaluation")
    assessment = read_json(root / "assessment/assessment.json")
    if (assessment["checkpointSha256"] != selection["checkpoint"]["sha256"] or
            not assessment["quantizedReference"]["passed"]):
        raise ValueError("Python export assessment failed or belongs to another checkpoint")
    gate = promotion_gate(report, setup["promotionPairs"])
    return {"campaignIdentity": setup["identity"], "selection": selection,
            "baselineSha256": setup["baselineModel"]["sha256"], **gate}


def promote(root, *, publish=False, push=False):
    root = Path(root).resolve()
    result = decision(root)
    from .quality import quality
    setup = read_json(root / "campaign.json")
    from .config import load_config
    config = load_config(setup["configs"][3]["path"])
    if not (root / "data-quality.json").exists():
        quality(config.data.manifest, root / "data-quality.json")
    if read_json(root / "data-quality.json")["uniquePositions"] < setup["minimumUnique"]:
        raise ValueError("Final dataset does not meet the campaign size requirement")
    model = Path(result["selection"]["export"]["path"])
    checkpoint = Path(result["selection"]["checkpoint"]["path"])
    manifest_path = model.parent / "manifest.json"
    manifest = read_json(manifest_path)
    manifest.update(runtimeCompatible=True, hasMeasuredPlayingStrength=True,
                    runtimeStatus="Selected checkpoint passed Python, native and WASM integer-reference parity",
                    promotion={k: result[k] for k in ("passed", "reasons", "summary", "paired", "criterion")})
    write_json(manifest_path, manifest)
    assessment_path = root / "assessment/assessment.json"
    assessment = read_json(assessment_path)
    assessment.update(runtimeCompatible=True, hasMeasuredPlayingStrength=True, promotion=manifest["promotion"])
    write_json(assessment_path, assessment)
    result.update(published=False)
    def record_publication():
        write_json(root / "publication.json", result)
        status = read_json(root / "status.json")
        status.update(modelPromoted=result["published"], publication=result,
                      updatedAt=datetime.now(timezone.utc).isoformat())
        write_json(root / "status.json", status)

    if not result["passed"] or not publish:
        record_publication()
        return result
    checksum = result["selection"]["export"]["sha256"]
    identifier = "rapfi-v3-" + checksum[:12]
    tag = "nnue-" + identifier
    pin_path = ROOT / "models/web-model.json"
    pin = read_json(pin_path)
    if pin["sha256"] not in (result["baselineSha256"], checksum):
        raise ValueError("The website already uses a different model; refusing to overwrite it")
    repo = json.loads(command("gh", "repo", "view", "--json", "nameWithOwner,url", capture=True).stdout)
    notes = root / "release-notes.md"
    s, p = result["summary"], result["paired"]
    notes.write_text(
        f"NNUE trained on the D4-deduplicated v3 corpus with unified expected-score and MultiPV targets.\n\n"
        f"Independent 3-second arena: {s['winsA']} wins / {s['draws']} draws / {s['winsB']} losses; "
        f"{p['pairs']} color-swapped opening pairs, one-sided paired sign p={p['oneSidedSignP']:.6g}.\n\n"
        f"Python, native and WASM integer reference checks passed. Model SHA-256: `{checksum}`.\n"
        f"Campaign identity: `{result['campaignIdentity']}`. Training checkpoint: `{checkpoint.name}`.\n",
        encoding="utf-8")
    assets = [model, manifest_path, checkpoint, root / "assessment/reference-vectors.json", assessment_path,
              root / "promotion-arena.json", root / "runtime.json"]
    # Exact tag/bytes make publication resumable without replacing an existing
    # release asset. Missing uploads can be retried with the same command.
    probe = subprocess.run(["gh", "release", "view", tag, "--json", "assets,url"], cwd=ROOT,
                           text=True, capture_output=True, encoding="utf-8",
                           creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
    if probe.returncode:
        command("gh", "release", "create", tag, *assets, "--title", identifier, "--notes-file", notes)
    else:
        existing = json.loads(probe.stdout)
        by_name = {a["name"]: a for a in existing["assets"]}
        missing = []
        for path in assets:
            if path.name not in by_name:
                missing.append(path)
            else:
                # GitHub exposes the SHA-256 digest for uploaded assets.
                expected = "sha256:" + file_sha256(path)
                if by_name[path.name].get("digest") != expected:
                    raise ValueError(f"Existing release asset digest differs or is unavailable: {path.name}")
        if missing:
            command("gh", "release", "upload", tag, *missing)
    fixture = ROOT / "tests/fixtures" / f"nnue-{identifier}.json"
    fixture.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(root / "assessment/reference-vectors.json", fixture)
    pin.update(id=identifier, label="Rapfi NNUE v3", bytes=model.stat().st_size, sha256=checksum,
               url=f"{repo['url']}/releases/download/{tag}/weights.gnn")
    write_json(pin_path, pin)
    staged = ROOT / "apps/web/public/models" / checksum / "weights.gnn"
    staged.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(model, staged)
    # The web bundle must resolve the newly pinned immutable asset.
    pnpm = shutil.which("pnpm.cmd" if sys.platform == "win32" else "pnpm")
    if not pnpm:
        raise ValueError("pnpm is required to validate the promoted website")
    command(pnpm, "exec", "prettier", "--write", pin_path, fixture)
    command(pnpm, "--filter", "@gomoku/web", "build")
    if push:
        paths = [str(pin_path.relative_to(ROOT)), str(fixture.relative_to(ROOT))]
        # A user may have unrelated staged work; use an explicit path commit.
        command("git", "add", "--", *paths)
        changed = command("git", "diff", "--cached", "--name-only", "--", *paths, capture=True).stdout.strip()
        if changed:
            command("git", "commit", "--only", "-m", "Promote arena-validated NNUE v3 for the browser", "--", *paths)
        command("git", "push", "origin", "HEAD")
    result.update(published=True, pushed=push, modelId=identifier, release=f"{repo['url']}/releases/tag/{tag}")
    record_publication()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("campaign", type=Path)
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--wait", action="store_true", help="Wait for an already running campaign to finish")
    args = parser.parse_args()
    if args.push and not args.publish:
        parser.error("--push requires --publish")
    if args.wait:
        while True:
            status = read_json(args.campaign / "status.json")
            if status["stage"] == "completed":
                break
            if status["stage"] in ("failed", "interrupted"):
                parser.exit(2, "Campaign stopped before publication; resume it first.\n")
            time.sleep(15)
    try:
        result = promote(args.campaign, publish=args.publish, push=args.push)
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as error:
        write_json(args.campaign / "publication-error.json", {"error": str(error)})
        parser.exit(2, f"Publication failed: {error}\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
