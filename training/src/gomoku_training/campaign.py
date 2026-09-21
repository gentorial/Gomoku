"""One recoverable weight-improvement run: data, training, held-out arena, parity.

Collection can run concurrently in ``gomoku_training.corpus``. This process
finishes missing collection/mining jobs itself if their external workers exit.
It only promotes candidates that pass the predeclared paired 3-second arena.
"""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import tempfile
from gomoku_tools.arena import identity as file_identity, paired_summary, summary, worker_match
from gomoku_tools.mine import mine, training_openings
from gomoku_tools.protocol import ROOT, binary
from gomoku_tools.rapfi import generate, generation_lock, load_config as load_teacher
from .config import digest, load_config
from .corpus import collect
from .io import file_sha256, read_json, write_json
from .prepare import prepare, source_records, teacher_inputs


def completed_sources(root, prefix="job"):
    root = Path(root)
    sources = []
    for path in sorted(root.glob(f"{prefix}-*.json")):
        if path.name.endswith(".record.json"):
            continue
        job = read_json(path)
        name = job["annotations"]
        if Path(name).name != name:
            raise ValueError("Invalid completed-job path")
        source = root / name
        if file_sha256(source) != job["files"][name]:
            raise ValueError("Completed source checksum mismatch")
        sources.append(source)
    return sources


def frozen_sources(path, sources=None):
    if not Path(path).exists():
        if not sources:
            raise ValueError("Cannot freeze an empty corpus")
        write_json(path, {"sources": [{"path": str(Path(p).resolve()), "sha256": file_sha256(p)} for p in sources]})
    result = []
    for source in read_json(path)["sources"]:
        if file_sha256(source["path"]) != source["sha256"]:
            raise ValueError("Frozen training source was modified")
        result.append(Path(source["path"]))
    return result


def holdout_openings(sources, output, *, split, count, excluded=(), seed=42):
    if Path(output).exists():
        return read_json(output)
    choices = {}
    seen_games = set()
    for path in sources:
        for sample in source_records(path):
            if sample["gameId"] in seen_games:
                continue
            seen_games.add(sample["gameId"])
            group = sample["openingId"]
            value = int(digest([seed, group])[:16], 16) / 2**64
            belongs = value < .1 if split == "test" else .1 <= value < .2
            score = sample["teacher"].get("score", {})
            if belongs and group not in excluded and score.get("kind") == "eval" and abs(score["value"]) <= 200:
                choices.setdefault(group, {"id": group, "moves": sample["position"]["moves"]})
    choices = sorted(choices.values(), key=lambda o: digest([seed, o["id"], split, "arena-v3"]))
    if len(choices) < count:
        raise ValueError(f"Need {count} balanced {split} opening groups; found {len(choices)}")
    result = {"format": "gomoku-openings-v1", "size": 15, "rule": "freestyle",
              "split": split, "seed": seed, "openings": choices[:count]}
    write_json(output, result)
    return result


def wait_or_finish(root, marker, finish):
    root = Path(root)
    while not (root / marker).exists():
        try:
            with generation_lock(root):
                pass
        except RuntimeError:
            time.sleep(10)
            continue
        finish()
        if not (root / marker).exists():
            raise RuntimeError(f"Stage returned without publishing {root / marker}")
    return read_json(root / marker)


def publish_directory(output, build):
    """Publish a complete export/assessment, retaining interrupted attempts."""
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        incomplete = output.with_name(output.name + f".incomplete-{time.time_ns()}").resolve()
        if incomplete.parent != output.parent or output.is_symlink():
            raise ValueError("Unexpected partial-stage path")
        os.replace(output, incomplete)
    staging = Path(tempfile.mkdtemp(prefix=output.name + ".building-", dir=output.parent)).resolve()
    if staging.parent != output.parent:
        raise ValueError("Unexpected stage publication path")
    # Failed attempts stay available for diagnosis; there is no recursive delete.
    build(staging)
    os.replace(staging, output)


def run_arena(model, baseline, openings, output, time_ms, engine, *, workers=4):
    opening_set = read_json(openings)
    report = {"format": "gomoku-arena-v1", "protocol": "worker",
              "engines": {"a": {"executable": file_identity(engine), "model": file_identity(model)},
                          "b": {"executable": file_identity(engine), "model": file_identity(baseline)}},
              "openings": file_identity(openings), "size": 15, "rule": "freestyle",
              "limits": {"timeMs": time_ms, "maxDepth": 12}}
    old_games = []
    if Path(output).exists():
        old = read_json(output)
        if any(old.get(k) != v for k, v in report.items()):
            raise ValueError("Arena identity changed on resume")
        old_games = old["games"]

    def save(games):
        report.update(summary=summary(games), paired=paired_summary(games), games=games,
                      execution={"requestedWorkers": workers, "unit": "opening-pair"})
        write_json(output, report)
        print(json.dumps({"event": "campaign-arena", "output": str(output), **report["summary"]}), flush=True)

    games = worker_match(engine, engine, opening_set["openings"], model_a=model, model_b=baseline,
                         time_ms=time_ms, depth=12, on_game=save, completed=old_games, workers=workers)
    save(games)
    return report


def promotion_gate(report, expected_pairs=32):
    stats, pairs = summary(report["games"]), paired_summary(report["games"])
    reasons = []
    if report["limits"]["timeMs"] != 3000:
        reasons.append("not the required 3-second budget")
    if stats["games"] != expected_pairs * 2 or pairs["pairs"] != expected_pairs:
        reasons.append("incomplete paired arena")
    if stats["failures"]:
        reasons.append("engine failures")
    if stats["scoreA"] <= stats["games"] / 2:
        reasons.append("candidate did not outscore incumbent")
    if pairs["oneSidedSignP"] > 0.05:
        reasons.append("paired sign test did not establish improvement")
    return {"passed": not reasons, "reasons": reasons, "summary": stats, "paired": pairs,
            "criterion": "32 independent color-swapped opening pairs, no failures, score > 50%, one-sided paired sign p <= 0.05"}


def campaign(root, *, general_config, multipv_config, warmup_config, training_config,
             baseline_checkpoint, baseline_model, minimum_unique=1_000_000, publish=False, push=False,
             arena_workers=4):
    if type(arena_workers) is not int or arena_workers < 1:
        raise ValueError("Arena workers must be a positive integer")
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    configs = [general_config, multipv_config, warmup_config, training_config]
    setup = {"configs": [file_identity(p) for p in configs],
             "baselineCheckpoint": file_identity(baseline_checkpoint),
             "baselineModel": file_identity(baseline_model), "engine": file_identity(binary()),
             "minimumUnique": minimum_unique, "developmentPairs": 16, "promotionPairs": 32,
             "selection": "best validation checkpoint and final checkpoint; development arena, then one fixed promotion arena"}
    run_identity = digest(setup)
    with generation_lock(root):
        setup_path = root / "campaign.json"
        if setup_path.exists() and read_json(setup_path)["identity"] != run_identity:
            raise ValueError("Campaign resume inputs changed")
        write_json(setup_path, {"identity": run_identity, **setup})

        def status(stage, **details):
            value = {"identity": run_identity, "stage": stage,
                     "updatedAt": datetime.now(timezone.utc).isoformat(), **details}
            write_json(root / "status.json", value)
            print(json.dumps({"event": "campaign-stage", **value}), flush=True)

        try:
            teacher_exe, general = load_teacher(general_config)
            _, multipv = load_teacher(multipv_config)
            mining_root = ROOT / "data/teachers" / (root.name + "-relabel")
            general_root = Path(general.output)
            warmup, training = load_config(warmup_config), load_config(training_config)
            if not (root / "warmup-sources.json").exists() and not completed_sources(general_root):
                status("initial-teacher-corpus")
                wait_or_finish(general_root, "manifest.json", lambda: generate(
                    teacher_exe, general, resume=(general_root / "generation.json").exists()))
            status("freeze-warmup")
            sources = frozen_sources(root / "warmup-sources.json", [
                *completed_sources(mining_root, "game"), *completed_sources(multipv.output),
                *completed_sources(general_root)])
            # Freeze independent arena openings before any candidate training.
            used = ROOT / "artifacts/arena/openings-used.json"
            excluded = set()
            if used.exists():
                old = read_json(used)
                excluded = {o["id"] for o in (old["openings"] if isinstance(old, dict) else old)}
            general_sources = completed_sources(general_root)
            holdout_openings(general_sources, root / "development-openings.json", split="validation", count=16, excluded=excluded)
            holdout_openings(general_sources, root / "promotion-openings.json", split="test", count=32, excluded=excluded)
            status("prepare-warmup", sourceFiles=len(sources))
            prepare(sources, Path(warmup.data.manifest).parent, resume=True)

            # Imports initialize the requested accelerator only when useful work
            # is ready. Warmup reuses the full network and becomes initialization
            # for the million-position run; it is not a discarded small model.
            from .train import load_checkpoint, run_training
            status("train-warmup", steps=warmup.run.steps)
            warmup_out = Path(warmup.run.output)
            resume = (warmup_out / "last.pt").exists()
            result = run_training(warmup, resume=resume, initialize=None if resume else baseline_checkpoint)
            if not result["completed"]:
                status("interrupted", interruptedStage="train-warmup")
                return
            status("collect-million", minimumUnique=minimum_unique)
            collection = wait_or_finish(root / "collection", "manifest.json", lambda: collect(
                general_config, root / "collection", minimum=minimum_unique, workers=8, wait_existing=True))
            if not collection["complete"] or collection["unique"] < minimum_unique:
                raise ValueError("Corpus did not reach its unique-position target")
            status("complete-multipv-and-relabel")
            wait_or_finish(multipv.output, "manifest.json", lambda: generate(teacher_exe, multipv, resume=True))
            selfplay_openings = root / "selfplay-openings.json"
            if not selfplay_openings.exists():
                training_openings([Path(c["path"]).parent for c in collection["corpora"]], selfplay_openings)
            wait_or_finish(mining_root, "manifest.json", lambda: mine(
                selfplay_openings, mining_root, baseline_model, teacher_exe))
            all_sources = [*completed_sources(mining_root, "game"), *teacher_inputs(Path(multipv.output) / "manifest.json")]
            for corpus in collection["corpora"]:
                if file_sha256(corpus["path"]) != corpus["sha256"]:
                    raise ValueError("Completed collection manifest changed")
                all_sources.extend(teacher_inputs(corpus["path"]))
            sources = frozen_sources(root / "training-sources.json", all_sources)
            status("prepare-million", sourceFiles=len(sources))
            dataset = prepare(sources, Path(training.data.manifest).parent, resume=True)
            if sum(dataset["counts"].values()) < minimum_unique:
                raise ValueError("Native-validated final dataset is smaller than the promised unique-position target")
            status("train-main", datasetCounts=dataset["counts"], steps=training.run.steps,
                   sampleVisits=training.run.steps * training.run.batch_size)
            train_out = Path(training.run.output)
            resume = (train_out / "last.pt").exists()
            result = run_training(training, resume=resume, initialize=None if resume else warmup_out / "best.pt")
            if not result["completed"]:
                status("interrupted", interruptedStage="train-main")
                return
            from .export import export_checkpoint
            from .assess import assess
            candidates = [train_out / "best.pt"]
            if load_checkpoint(train_out / "last.pt")["step"] != load_checkpoint(candidates[0])["step"]:
                candidates.append(train_out / "last.pt")
            status("select-on-development-arena", candidates=[str(c) for c in candidates])
            evaluations = []
            for checkpoint in candidates:
                export_dir = root / ("export-" + checkpoint.stem)
                export_manifest = export_dir / "manifest.json"
                if not export_manifest.exists():
                    publish_directory(export_dir, lambda staging: export_checkpoint(checkpoint, staging, device="auto"))
                if read_json(export_manifest)["checkpointSha256"] != file_sha256(checkpoint):
                    raise ValueError("Export identity does not match candidate")
                report = run_arena(export_dir / "weights.gnn", baseline_model, root / "development-openings.json",
                                   root / f"development-{checkpoint.stem}.json", 300, binary(), workers=arena_workers)
                evaluations.append((report["summary"]["scoreA"], -report["summary"]["failures"], checkpoint, export_dir))
            # Failure-free candidates take precedence; ties favor validation best.
            selected = max(evaluations, key=lambda item: (item[1], item[0]))
            _, _, checkpoint, export_dir = selected
            selection = {"checkpoint": file_identity(checkpoint), "export": file_identity(export_dir / "weights.gnn"),
                         "candidates": [{"checkpoint": str(c), "developmentScore": s, "failures": -f}
                                        for s, f, c, _ in evaluations]}
            write_json(root / "selection.json", selection)
            status("promotion-arena", selected=str(checkpoint), timeMs=3000, pairs=32, arenaWorkers=arena_workers)
            report = run_arena(export_dir / "weights.gnn", baseline_model, root / "promotion-openings.json",
                               root / "promotion-arena.json", 3000, binary(), workers=arena_workers)
            gate = promotion_gate(report)
            write_json(root / "promotion.json", {**gate, "selection": selection})
            status("heldout-and-runtime-assessment", promotionGate=gate["passed"])
            assessment_dir = root / "assessment"
            if not (assessment_dir / "assessment.json").exists():
                publish_directory(assessment_dir, lambda staging: assess(
                    checkpoint, baseline_checkpoint, training.data.manifest, export_dir / "manifest.json",
                    staging, device="auto", positions=8))
            assessment = read_json(assessment_dir / "assessment.json")
            if not assessment["quantizedReference"]["passed"]:
                raise ValueError("The completed assessment failed quantized export parity")
            # Verify the exact selected bytes in native and WASM, even if arena
            # rejects promotion. Model publication is a separate explicit command.
            parity = root / "runtime.json"
            if not parity.exists():
                subprocess.run(["node", str(ROOT / "scripts/verify-candidate.mjs"), str(export_dir / "weights.gnn"),
                                str(assessment_dir / "reference-vectors.json"), str(parity)], check=True,
                               cwd=ROOT, creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
            status("completed", datasetCounts=dataset["counts"], training=result, promotion=gate,
                   selected=selection, runtime=read_json(parity), modelPromoted=False)
            from .promote import promote
            publication = promote(root, publish=publish, push=push)
            status("completed", datasetCounts=dataset["counts"], training=result, promotion=gate,
                   selected=selection, runtime=read_json(parity), modelPromoted=publication["published"],
                   publication=publication)
        except BaseException as error:
            status("failed", error=f"{type(error).__name__}: {error}")
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/campaigns/rapfi-v3")
    parser.add_argument("--general-config", type=Path, default=ROOT / "tools/configs/rapfi-v3-general.toml")
    parser.add_argument("--multipv-config", type=Path, default=ROOT / "tools/configs/rapfi-v3-multipv.toml")
    parser.add_argument("--warmup-config", type=Path, default=ROOT / "training/configs/rapfi-v3-warmup.toml")
    parser.add_argument("--training-config", type=Path, default=ROOT / "training/configs/rapfi-v3.toml")
    parser.add_argument("--baseline-checkpoint", type=Path, default=ROOT / "artifacts/training/rapfi-calibrated-v1/best.pt")
    parser.add_argument("--baseline-model", type=Path, default=ROOT / "artifacts/models/rapfi-calibrated-v1/weights.gnn")
    parser.add_argument("--minimum-unique", type=int, default=1_000_000)
    parser.add_argument("--arena-workers", type=int, default=4, help="Concurrent opening pairs during evaluation")
    parser.add_argument("--publish", action="store_true", help="Publish only if the independent promotion gate passes")
    parser.add_argument("--push", action="store_true", help="Commit and push the promoted website pin and reference fixture")
    args = parser.parse_args()
    if args.push and not args.publish:
        parser.error("--push requires --publish")
    campaign(args.output, general_config=args.general_config, multipv_config=args.multipv_config,
             warmup_config=args.warmup_config, training_config=args.training_config,
             baseline_checkpoint=args.baseline_checkpoint, baseline_model=args.baseline_model,
             minimum_unique=args.minimum_unique, publish=args.publish, push=args.push,
             arena_workers=args.arena_workers)


if __name__ == "__main__":
    main()
