"""Pinned Rapfi teacher installation, resumable self-play jobs and verified labels."""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import asdict, dataclass, fields
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import time
import tomllib
import urllib.request
from .protocol import ROOT, Worker
from .teacher_data import FORMAT, annotated_game, read_games

RELEASE = "250615"
SOURCE_COMMIT = "1be1551ced57e38d53ed58f6d74bf6f8b4bdc230"
NETWORK_COMMIT = "918b757a129258e9e765f77fe17d507c2bb1a60b"
ARCHIVE_URL = (
    f"https://github.com/dhbloo/rapfi/releases/download/{RELEASE}/Rapfi-engine.7z"
)
ARCHIVE_SHA256 = "1a3e24024062a153ac079060ee9589a37c6bdd1ecc54fed3908793c519594e05"
LAUNCH_LOCK = threading.Lock()


@contextmanager
def generation_lock(root):
    """An OS lock is released even if the collector crashes or is terminated."""
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".generation.lock").open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise RuntimeError("Another collector is writing this directory") from error
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def publish(path, writer):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            writer(stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def write_json(path, value):
    publish(path, lambda stream: json.dump(value, stream, indent=2, allow_nan=False))


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def creation_flags():
    return subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def install(output):
    """Download the official immutable release, verify its hash, extract with bsdtar."""
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    archive = output / "Rapfi-engine.7z"
    if not archive.exists():
        temporary = output / "Rapfi-engine.7z.download"
        request = urllib.request.Request(
            ARCHIVE_URL, headers={"User-Agent": "Gomoku-teacher-setup"}
        )
        with (
            urllib.request.urlopen(request, timeout=60) as response,
            temporary.open("wb") as stream,
        ):
            shutil.copyfileobj(response, stream)
        if sha256(temporary) != ARCHIVE_SHA256:
            raise ValueError("Rapfi archive checksum mismatch")
        os.replace(temporary, archive)
    if sha256(archive) != ARCHIVE_SHA256:
        raise ValueError("Rapfi archive checksum mismatch")
    tar = shutil.which("bsdtar") or shutil.which("tar")
    if not tar:
        raise ValueError(
            "Install bsdtar/libarchive (Windows built-in tar supports .7z)"
        )
    listing = subprocess.run(
        [tar, "-tf", str(archive)], capture_output=True, text=True, check=True
    )
    names = listing.stdout.splitlines()
    if not names or any(not re.fullmatch(r"[A-Za-z0-9_.-]+", name) for name in names):
        raise ValueError("Unexpected release archive paths")
    stamp = output / "installation.json"
    if stamp.exists():
        old = read_json(stamp)
        if old.get("archiveSha256") != ARCHIVE_SHA256 or any(
            not (output / name).is_file() or sha256(output / name) != value
            for name, value in old["files"].items()
        ):
            raise ValueError(
                "Installed teacher changed; use a new installation directory"
            )
        return old
    # Validate existing files against the verified archive without overwriting edits.
    staging = Path(tempfile.mkdtemp(prefix=".unpack-", dir=output)).resolve()
    if staging.parent != output:
        raise ValueError("Unexpected installation staging path")
    try:
        subprocess.run([tar, "-xf", str(archive), "-C", str(staging)], check=True)
        for name in names:
            source, target = staging / name, output / name
            if source.is_symlink() or not source.is_file():
                raise ValueError("Unexpected archive entry type")
            if target.exists() and sha256(target) != sha256(source):
                raise ValueError(f"Existing teacher file differs from release: {name}")
            if not target.exists():
                shutil.copy2(source, target)
    finally:
        # Release archive is flat; no recursive deletion is needed.
        for path in staging.iterdir():
            if not path.is_dir():
                path.unlink()
        staging.rmdir()
    licenses = {
        "ENGINE-LICENSE.txt": f"https://raw.githubusercontent.com/dhbloo/rapfi/{SOURCE_COMMIT}/Copying.txt",
        "NETWORK-LICENSE.txt": f"https://raw.githubusercontent.com/dhbloo/rapfi-networks/{NETWORK_COMMIT}/LICENSE",
    }
    for name, url in licenses.items():
        if not (output / name).exists():
            with urllib.request.urlopen(url, timeout=30) as response:
                (output / name).write_bytes(response.read())
    result = {
        "release": RELEASE,
        "sourceCommit": SOURCE_COMMIT,
        "networkCommit": NETWORK_COMMIT,
        "archiveUrl": ARCHIVE_URL,
        "archiveSha256": ARCHIVE_SHA256,
        "files": {name: sha256(output / name) for name in names},
        "licenses": licenses,
    }
    write_json(stamp, result)
    return result


@dataclass(frozen=True)
class Generation:
    output: str = "data/teachers/rapfi-v1"
    jobs: int = 16
    games_per_job: int = 16
    workers: int = 4
    nodes: int = 100000
    hash_mb: int = 64
    size: int = 15
    rule: str = "freestyle"
    opening_min: int = 6
    opening_max: int = 10
    balance_nodes: int = 100000
    job_timeout_seconds: int = 1800
    multipv: int = 1
    multipv_decay_steps: int = 0
    max_plies: int = 0
    samples_per_game: int = 0

    def validate(self):
        for field in fields(self):
            if field.name in ("output", "rule"):
                continue
            value = getattr(self, field.name)
            if field.name in ("multipv_decay_steps", "max_plies", "samples_per_game"):
                if type(value) is not int or value < 0:
                    raise ValueError(f"{field.name} must be a nonnegative integer")
                continue
            if type(value) is not int or value < 1:
                raise ValueError(f"{field.name} must be a positive integer")
        if not 1 <= self.workers <= 16 or not 16 <= self.hash_mb <= 4096:
            raise ValueError("Use 1..16 workers and 16..4096 MiB hash per worker")
        if (self.rule, self.size) not in (
            ("freestyle", 15),
            ("freestyle", 20),
            ("standard", 15),
        ):
            raise ValueError("Requested rule/size has no supported pinned NNUE teacher")
        if not 6 <= self.opening_min <= self.opening_max <= 20:
            raise ValueError("Require 6 <= opening_min <= opening_max <= 20")
        if self.multipv > 32 or self.samples_per_game > self.size*self.size:
            raise ValueError("MultiPV <= 32 and samples_per_game <= board area required")
        if self.max_plies and not self.opening_max < self.max_plies <= self.size*self.size:
            raise ValueError("max_plies must follow the opening and fit the board")


def load_config(path):
    path = Path(path).resolve()
    with path.open("rb") as stream:
        value = tomllib.load(stream)
    if set(value) != {"version", "teacher", "generation"} or value["version"] != 1:
        raise ValueError("Unsupported teacher configuration")
    if set(value["teacher"]) != {"executable"}:
        raise ValueError("Teacher requires exactly an executable path")
    options = value["generation"]
    if set(options) - {field.name for field in fields(Generation)}:
        raise ValueError("Unknown generation option")
    options["output"] = str(
        (path.parent / options.get("output", Generation.output)).resolve()
    )
    config = Generation(**options)
    config.validate()
    return (path.parent / value["teacher"]["executable"]).resolve(), config


def identify(executable):
    executable = Path(executable).resolve()
    root = executable.parent
    installation = read_json(root / "installation.json")
    if installation.get("archiveSha256") != ARCHIVE_SHA256:
        raise ValueError("Teacher must use the pinned Rapfi installation")
    files = {
        name: sha256(root / name)
        for name in installation["files"]
        if name == executable.name
        or name == "config.toml"
        or name.endswith((".bin", ".bin.lz4"))
    }
    if executable.name not in files or any(
        value != installation["files"][name] for name, value in files.items()
    ):
        raise ValueError("Teacher executable, config or weights changed")
    with (root / "config.toml").open("rb") as stream:
        config = tomllib.load(stream)
    if (
        config["model"]["evaluator"]["type"] != "mix9svq"
        or config["model"].get("scaling_factor", 200) != 200
    ):
        raise ValueError("Expected mix9svq and Rapfi's default score scale of 200")
    result = {
        "engine": "Rapfi",
        "release": RELEASE,
        "sourceCommit": SOURCE_COMMIT,
        "networkCommit": NETWORK_COMMIT,
        "evaluator": "mix9svq",
        "files": files,
        "scoreConvention": "side-to-move; winrate=sigmoid(eval/200); mate=abs(eval)>=29500",
    }
    return {"id": "rapfi-" + digest(result)[:20], **result}


def preflight(executable, config):
    commands = [
        "ABOUT",
        "INFO thread_num 1",
        "INFO usedatabase 0",
        f"INFO rule {0 if config.rule == 'freestyle' else 1}",
        f"START {config.size}",
        "TRACESEARCH",
        "END",
        "",
    ]
    result = subprocess.run(
        [str(executable), "--force-utf8"],
        input="\n".join(commands),
        cwd=executable.parent,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        creationflags=creation_flags(),
    )
    output = result.stdout + result.stderr
    if (
        result.returncode
        or "ERROR" in output
        or "nnue: load weight from" not in output
        or "Evaluator Eval[Black]:" not in output
        or "SF 200.00" not in output
    ):
        raise RuntimeError(f"Rapfi NNUE preflight failed:\n{output[-4000:]}")
    return output


def command(executable, config, raw):
    return [
        str(executable),
        "selfplay",
        "--number",
        str(config.games_per_job),
        "--boardsize",
        str(config.size),
        "--rule",
        config.rule,
        "--thread",
        "1",
        "--hashsize",
        str(config.hash_mb),
        "--mean-nodes",
        str(config.nodes),
        "--var-nodes",
        "0",
        "--min-nodes",
        str(config.nodes),
        "--min-move",
        str(config.opening_min),
        "--max-move",
        str(config.opening_max),
        "--balance1-node",
        str(config.balance_nodes),
        "--balance2-node",
        str(config.balance_nodes * 2),
        "--draw-count",
        "0",
        "--mate-ply",
        "1",
        "--multipv",
        str(config.multipv),
        "--multipv-decay-steps",
        str(config.multipv_decay_steps),
        "--no-multipv-after-mate",
        "--force-draw-ply",
        str(config.max_plies),
        "--output-type",
        "binpack",
        "--output",
        str(raw),
        "--report-interval",
        "10000",
        "-q",
    ]


def convert(raw, annotations, games_path, teacher, samples_per_game=0):
    counts = {
        "games": 0,
        "positions": 0,
        "terminalGames": 0,
        "ordinaryScores": 0,
        "mateScores": 0,
        "missingScores": 0,
    }
    source = {"file": raw.name, "sha256": sha256(raw)}

    def write_annotations(stream):
        def write_games(game_stream):
            with Worker() as worker:
                for game in read_games(raw):
                    records, record = annotated_game(game, worker, teacher, source, samples_per_game)
                    counts["games"] += 1
                    counts["terminalGames"] += record["status"] != "playing"
                    game_stream.write(json.dumps(record, allow_nan=False) + "\n")
                    for sample in records:
                        stream.write(json.dumps(sample, allow_nan=False) + "\n")
                        counts["positions"] += 1
                        kind = sample["teacher"].get("score", {}).get("kind")
                        counts[
                            {
                                "eval": "ordinaryScores",
                                "mate": "mateScores",
                                None: "missingScores",
                            }[kind]
                        ] += 1

        publish(games_path, write_games)

    publish(annotations, write_annotations)
    return counts


def run_job(index, executable, config, teacher, identity, stop):
    # Rapfi's signal handler also catches native faults and can exit with code
    # zero before its requested game count. Retry a bounded number of times;
    # never publish the truncated binpack as a successful batch.
    for attempt in range(3):
        try:
            return run_job_once(index, executable, config, teacher, identity, stop)
        except RuntimeError as error:
            if (stop.is_set() or attempt == 2 or not str(error).startswith(
                    ("Rapfi exited with code", "Teacher did not finish job"))):
                raise
            root = Path(config.output)
            log = root / f"job-{index:05d}.log"
            if log.exists():
                stamp = time.time_ns()
                shutil.copyfile(log, root / f"job-{index:05d}.failed-{stamp}.log")
            print(json.dumps({"event": "teacher-retry", "job": index,
                              "attempt": attempt + 2, "reason": str(error)[:240]}), flush=True)


def run_job_once(index, executable, config, teacher, identity, stop):
    root = Path(config.output)
    stem = f"job-{index:05d}"
    summary = root / (stem + ".json")
    if summary.exists():
        old = read_json(summary)
        if old.get("identity") != identity:
            raise ValueError("Existing teacher job has a different configuration")
        for name, value in old["files"].items():
            if Path(name).name != name or sha256(root / name) != value:
                raise ValueError("Existing teacher job checksum mismatch")
        return old
    if stop.is_set():
        raise RuntimeError("Teacher generation interrupted")
    raw = root / (stem + ".binpack")
    temporary = root / (stem + ".binpack.partial")
    log_path = root / (stem + ".log")
    args = command(executable, config, temporary)
    started = time.monotonic()
    with log_path.open("wb") as log:
        # This release seeds selfplay from a millisecond clock without a CLI seed.
        # Separate launches to avoid duplicate streams when jobs start together.
        with LAUNCH_LOCK:
            if stop.is_set():
                raise RuntimeError("Teacher generation interrupted")
            process = subprocess.Popen(
                args,
                cwd=executable.parent,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=creation_flags(),
            )
            time.sleep(0.25)
        try:
            while process.poll() is None:
                if (
                    stop.wait(0.2)
                    or time.monotonic() - started > config.job_timeout_seconds
                ):
                    raise RuntimeError(
                        "Teacher job interrupted or exceeded its timeout"
                    )
            if process.returncode:
                raise RuntimeError(f"Rapfi exited with code {process.returncode}")
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
    log = log_path.read_text(encoding="utf-8", errors="replace")
    if "ERROR" in log or f"Completed playing {config.games_per_job} games." not in log:
        raise RuntimeError(f"Teacher did not finish job {index}: {log[-2000:]}")
    os.replace(temporary, raw)
    annotations, games_path = (
        root / (stem + ".annotations.jsonl"),
        root / (stem + ".games.jsonl"),
    )
    counts = convert(raw, annotations, games_path, teacher, config.samples_per_game)
    if counts["games"] != config.games_per_job:
        raise ValueError("Teacher produced an unexpected game count")
    result = {
        "identity": identity,
        "index": index,
        **counts,
        "seconds": time.monotonic() - started,
        "command": args,
        "annotations": annotations.name,
        "files": {
            path.name: sha256(path) for path in (raw, annotations, games_path, log_path)
        },
    }
    write_json(summary, result)
    return result


def generate(executable, config, resume=False):
    with generation_lock(Path(config.output)):
        return generate_locked(executable, config, resume)


def generate_locked(executable, config, resume=False):
    config.validate()
    root = Path(config.output)
    teacher = identify(executable)
    teacher["search"] = {
        "requestedNodes": config.nodes,
        "threads": 1,
        "hashMiB": config.hash_mb,
        "multipv": config.multipv,
        "actualNodesRecorded": False,
    }
    settings = asdict(config)
    settings.pop("output")
    settings.pop("workers")
    for name in ("multipv", "multipv_decay_steps", "max_plies", "samples_per_game"):
        if settings[name] == getattr(Generation(), name):
            settings.pop(name)
    identity = digest({"teacher": teacher, "generation": settings, "format": FORMAT})
    setup = root / "generation.json"
    if any(path.name != ".generation.lock" for path in root.iterdir()):
        if (
            not resume
            or not setup.exists()
            or read_json(setup).get("identity") != identity
        ):
            raise ValueError(
                "Output already exists; resume identical configuration or use a new output"
            )
    root.mkdir(parents=True, exist_ok=True)
    write_json(
        setup,
        {
            "kind": "gomoku-teacher-generation-v1",
            "identity": identity,
            "teacher": teacher,
            "config": asdict(config),
            "format": FORMAT,
            "openingRandomness": "Rapfi seeds PRNG from monotonic clock; raw games are preserved",
            "launchSpacingSeconds": 0.25,
        },
    )
    (root / "preflight.log").write_text(preflight(executable, config), encoding="utf-8")
    stop = threading.Event()
    completed, failures = [], []
    pool = ThreadPoolExecutor(max_workers=config.workers)
    futures = [
        pool.submit(run_job, i, executable, config, teacher, identity, stop)
        for i in range(config.jobs)
    ]
    try:
        for future in as_completed(futures):
            try:
                job = future.result()
                completed.append(job)
                print(
                    json.dumps(
                        {
                            "event": "teacher-job",
                            "completed": len(completed),
                            "total": config.jobs,
                            "job": job["index"],
                            "games": job["games"],
                            "positions": job["positions"],
                        }
                    ),
                    flush=True,
                )
            except Exception as error:
                failures.append(str(error))
                stop.set()
    except BaseException:
        stop.set()
        raise
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
    if failures:
        raise RuntimeError(
            "Teacher generation failed; successful jobs can be resumed: " + failures[0]
        )
    if identify(executable)["id"] != teacher["id"]:
        raise ValueError("Teacher files changed during generation")
    result = corpus_manifest(identity, teacher, completed, config.jobs)
    write_json(root / "manifest.json", result)
    return result


def corpus_manifest(identity, teacher, completed, requested_jobs):
    return {
        "kind": "gomoku-teacher-corpus-v1",
        "identity": identity,
        "teacher": teacher,
        "generationComplete": len(completed) == requested_jobs,
        "requestedJobs": requested_jobs,
        "jobs": sorted(completed, key=lambda job: job["index"]),
        "counts": {
            key: sum(job[key] for job in completed)
            for key in (
                "games",
                "positions",
                "terminalGames",
                "ordinaryScores",
                "mateScores",
                "missingScores",
            )
        },
    }


def snapshot(source, output):
    """Freeze completed jobs while generation continues; never read partial games."""
    source, output = Path(source).resolve(), Path(output).resolve()
    setup = read_json(source / "generation.json")
    if setup.get("kind") != "gomoku-teacher-generation-v1":
        raise ValueError("Expected a teacher generation directory")
    if output == source or output.is_relative_to(source):
        raise ValueError("Snapshot must be outside the generation directory")
    if output.exists() and any(output.iterdir()):
        raise ValueError("Snapshot output must be empty")
    completed = []
    for path in sorted(source.glob("job-*.json")):
        if not re.fullmatch(r"job-[0-9]{5,}\.json", path.name):
            continue
        job = read_json(path)
        if job.get("identity") != setup["identity"]:
            raise ValueError("Job does not belong to this teacher run")
        for name, value in job["files"].items():
            if (
                not re.fullmatch(r"[A-Za-z0-9_.-]+", name)
                or sha256(source / name) != value
            ):
                raise ValueError("Teacher job checksum mismatch")
        completed.append(job)
    if not completed:
        raise ValueError("No completed teacher jobs to snapshot")
    output.mkdir(parents=True, exist_ok=True)
    for job in completed:
        for name, value in job["files"].items():
            shutil.copyfile(source / name, output / name)
            if sha256(output / name) != value:
                raise ValueError("Snapshot checksum mismatch")
        write_json(output / f"job-{job['index']:05d}.json", job)
    result = corpus_manifest(
        setup["identity"], setup["teacher"], completed, setup["config"]["jobs"]
    )
    result["snapshot"] = True
    result["generationSha256"] = sha256(source / "generation.json")
    write_json(output / "manifest.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    setup = sub.add_parser(
        "install", help="Download and verify the pinned official release"
    )
    setup.add_argument(
        "--output", type=Path, default=ROOT / ".tools" / f"rapfi-{RELEASE}"
    )
    collect = sub.add_parser(
        "generate", help="Generate genuine NNUE teacher games and annotations"
    )
    collect.add_argument(
        "--config", type=Path, default=ROOT / "tools/configs/rapfi.toml"
    )
    collect.add_argument("--resume", action="store_true")
    freeze = sub.add_parser(
        "snapshot", help="Freeze completed jobs for concurrent training"
    )
    freeze.add_argument("source", type=Path)
    freeze.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.action == "install":
            result = install(args.output)
            print(
                json.dumps(
                    {
                        "release": result["release"],
                        "directory": str(args.output.resolve()),
                        "archiveSha256": result["archiveSha256"],
                    },
                    indent=2,
                )
            )
        elif args.action == "generate":
            result = generate(*load_config(args.config), resume=args.resume)
            print(json.dumps(result["counts"], indent=2))
        else:
            result = snapshot(args.source, args.output)
            print(json.dumps(result["counts"], indent=2))
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as error:
        parser.exit(2, f"Teacher pipeline failed: {error}\n")


if __name__ == "__main__":
    main()
