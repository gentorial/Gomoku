from dataclasses import replace
import contextlib
import io
import json
from pathlib import Path
import tempfile
import subprocess
import struct
import sys
import unittest
from unittest import mock
import numpy as np
import torch
from gomoku_training.config import Config, DataConfig, ModelConfig, RunConfig, from_dict
from gomoku_training.features import (
    enumerate_patterns,
    pattern_ids,
    PATTERN_COUNT,
    line_geometry,
)
from gomoku_training.model import LineNNUE
from gomoku_training.loss import loss_terms
from gomoku_training.prepare import prepare, pack_sample
from gomoku_training.shards import BlockSampler, ShardDataset, decode_batch
from gomoku_training.train import load_checkpoint, run_training
from gomoku_training.export import export_checkpoint, QuantizedReference, round_divide
from gomoku_training import prepare as preparation
from gomoku_training.io import file_sha256
from gomoku_training.assess import reference_check
from gomoku_training.evaluate import evaluate


def fixture_samples(count=24, size=15):
    rng = np.random.default_rng(75)
    for i in range(count):
        cells = rng.choice(size * size, 8, replace=False).tolist()
        moves = [{"x": point % size, "y": point // size} for point in cells]
        yield {
            "format": "gomoku-sample-v1",
            "gameId": f"game-{i}",
            "openingId": f"opening-{i}",
            "position": {"size": size, "rule": "freestyle", "moves": moves[:6]},
            "result": "black_win" if i % 2 else "white_win",
            "teacher": {
                "id": "test-fixture-only",
                "perspective": "side_to_move",
                "score": {"kind": "eval", "value": (i - 12) * 20},
            },
            "policy": {"kind": "best_move", "moves": [moves[6]]},
        }


def make_data(root, count=24):
    path = root / "samples.jsonl"
    path.write_text(
        "".join(json.dumps(item) + "\n" for item in fixture_samples(count)),
        encoding="utf-8",
    )
    prepare([path], root / "data", shard_size=5)
    return root / "data/manifest.json"


class TrainingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        torch.set_num_threads(2)

    def test_config_strictness(self):
        for value in (
            {"run": {"batch_siz": 2}},
            {"model": {"architecture": "toy"}},
            {"run": {"learning_rate": float("nan")}},
            {"run": {"steps": True}},
        ):
            with self.assertRaises(ValueError):
                from_dict(value)

    def test_bordered_features_and_lookup_encoding(self):
        self.assertEqual(PATTERN_COUNT, 397488)
        board = np.zeros((15, 15), dtype=np.uint8)
        board[0, 0], board[1, 1], board[0, 2] = 1, 2, 1
        indices, _ = line_geometry(15)
        cells = np.concatenate((board.reshape(-1), [3]))
        for perspective in (1, 2):
            ids = pattern_ids(board, perspective)
            for d, point in ((0, 0), (1, 15), (2, 16), (3, 0), (2, 224), (0, 112)):
                planes = enumerate_patterns(int(ids[d, point]), int(ids[d, point]) + 1)[
                    0
                ]
                line = cells[indices[d, point]]
                np.testing.assert_array_equal(planes[0], line == perspective)
                np.testing.assert_array_equal(planes[1], line == 3 - perspective)
                np.testing.assert_array_equal(planes[2], line == 3)

    def test_sampler_resume_and_epoch_coverage(self):
        sampler = BlockSampler(23, 7, 5)
        self.assertEqual(sorted(sampler.next_indices(23)), list(range(23)))
        sampler.next_indices(9)
        resumed = BlockSampler(23, 7, 5, sampler.state_dict())
        self.assertEqual(sampler.next_indices(60), resumed.next_indices(60))

    def test_missing_labels_and_policy_validation(self):
        sample = next(fixture_samples())
        sample.pop("result")
        row, _ = pack_sample(sample)
        self.assertEqual(row["result"], -1)
        self.assertTrue(np.isnan(row["teacher_wdl"]).all())
        sample["policy"]["moves"] = [sample["position"]["moves"][0]]
        with self.assertRaises(ValueError):
            pack_sample(sample)

    def test_shards_deduplicate_and_verify(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            records = list(fixture_samples())
            duplicate = json.loads(json.dumps(records[0]))
            for move in duplicate["position"]["moves"] + duplicate["policy"]["moves"]:
                move["x"], move["y"] = 14 - move["y"], move["x"]
            records.append(duplicate)
            source = root / "source.jsonl"
            source.write_text("\n".join(map(json.dumps, records)), encoding="utf-8")
            manifest = prepare([source], root / "data", shard_size=4)
            self.assertEqual(manifest["duplicatesRemoved"], 1)
            self.assertEqual(sum(manifest["counts"].values()), 24)
            self.assertTrue(all(manifest["counts"].values()))
            self.assertEqual(
                prepare([source], root / "data", shard_size=4, resume=True), manifest
            )
            dataset = ShardDataset(root / "data/manifest.json", "train")
            try:
                batch = decode_batch([dataset[0]], 15, np.random.default_rng(9))
                self.assertEqual(int((batch["boards"] != 0).sum()), 6)
                self.assertEqual(float(batch["policy"].sum()), 1)
                self.assertEqual(
                    float(
                        (batch["policy"] * (batch["boards"].reshape(1, -1) != 0)).sum()
                    ),
                    0,
                )
            finally:
                dataset.close()
            shard = root / "data" / manifest["shards"][0]["file"]
            with shard.open("r+b") as stream:
                stream.seek(-1, 2)
                stream.write(b"x")
            with self.assertRaisesRegex(ValueError, "checksum"):
                ShardDataset(
                    root / "data/manifest.json", manifest["shards"][0]["split"]
                )

    def test_formal_model_forward_backward_both_sizes(self):
        model = LineNNUE(
            ModelConfig(mapping_width=8, channels=8, value_hidden=16, policy_hidden=8)
        )
        for size in (15, 20):
            sample = next(fixture_samples(size=size))
            row, _ = pack_sample(sample)
            batch = {
                k: torch.from_numpy(v) for k, v in decode_batch([row], size).items()
            }
            output = model(batch["boards"], batch["turn"])
            self.assertEqual(output["value"].shape, (1, 3))
            self.assertEqual(output["policy"].shape, (1, size * size))
            loss, _ = loss_terms(output, batch, Config().loss)
            loss.backward()
            self.assertTrue(torch.isfinite(loss))
            self.assertTrue(
                all(
                    p.grad is not None and torch.isfinite(p.grad).all()
                    for p in model.parameters()
                )
            )
            model.zero_grad(set_to_none=True)

    def test_missing_labels_do_not_contribute_to_loss(self):
        samples = list(fixture_samples())[:3]
        samples[0].pop("teacher")
        samples[0].pop("policy")  # result only
        samples[1].pop("result")
        samples[1].pop("policy")
        samples[1]["teacher"]["score"]["value"] = 0  # teacher only, zero scalar
        samples[2].pop("result")
        samples[2].pop("teacher")  # policy only
        rows = [pack_sample(sample)[0] for sample in samples]
        batch = {k: torch.from_numpy(v) for k, v in decode_batch(rows, 15).items()}
        prediction = {
            "value": torch.zeros((3, 3), requires_grad=True),
            "policy": torch.zeros((3, 225), requires_grad=True),
        }
        loss, metrics = loss_terms(prediction, batch, Config().loss)
        self.assertAlmostEqual(
            float(loss.detach()), (np.log(3) + np.log(225)) / 3, places=6
        )
        self.assertEqual(int(metrics["effective"]), 3)
        self.assertEqual(int(metrics["teacherCount"]), 1)
        self.assertEqual(int(metrics["resultCount"]), 1)
        loss.backward()
        self.assertEqual(float(prediction["value"].grad[2].abs().sum()), 0)
        self.assertEqual(float(prediction["policy"].grad[:2].abs().sum()), 0)

    def test_preparation_resumes_after_committed_records(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.jsonl"
            source.write_text(
                "\n".join(map(json.dumps, fixture_samples(280))), encoding="utf-8"
            )
            original = preparation.source_records

            def interrupted(path):
                for i, record in enumerate(original(path)):
                    if i == 270:
                        raise RuntimeError("Interrupted import")
                    yield record

            with mock.patch.object(preparation, "source_records", interrupted):
                with self.assertRaisesRegex(RuntimeError, "Interrupted"):
                    prepare([source], root / "resumed", shard_size=40)
            self.assertFalse((root / "resumed/manifest.json").exists())
            resumed = prepare([source], root / "resumed", shard_size=40, resume=True)
            whole = prepare([source], root / "whole", shard_size=40)
            self.assertEqual(resumed, whole)

    def test_teacher_corpus_checksums_and_split_provenance(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "job-00000.annotations.jsonl"
            source.write_text(
                "\n".join(map(json.dumps, fixture_samples())), encoding="utf-8"
            )
            corpus = root / "manifest.json"
            corpus.write_text(
                json.dumps(
                    {
                        "kind": "gomoku-teacher-corpus-v1",
                        "identity": "corpus-id",
                        "jobs": [
                            {
                                "identity": "corpus-id",
                                "annotations": source.name,
                                "files": {source.name: file_sha256(source)},
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            result = prepare([], root / "prepared", teacher_corpus=corpus)
            self.assertEqual(sum(result["counts"].values()), 24)
            self.assertEqual(
                result["settings"]["teacherCorpusSha256"], file_sha256(corpus)
            )
            source.write_text("corrupt", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "checksum"):
                prepare([], root / "corrupt-data", teacher_corpus=corpus)

    def test_gradient_accumulation_matches_full_batch_update(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            root = Path(temporary)
            manifest = make_data(root)
            config = Config(
                model=ModelConfig(
                    mapping_width=4, channels=4, value_hidden=8, policy_hidden=4
                ),
                data=DataConfig(str(manifest)),
                run=RunConfig(
                    output=str(root / "micro"),
                    device="cpu",
                    steps=1,
                    batch_size=4,
                    validation_batches=1,
                    cpu_threads=2,
                ),
            )
            run_training(config)
            run_training(
                replace(
                    config,
                    run=replace(
                        config.run, output=str(root / "full"), micro_batch_size=4
                    ),
                )
            )
            a, b = (
                load_checkpoint(root / "micro/last.pt"),
                load_checkpoint(root / "full/last.pt"),
            )
            for key in a["model"]:
                torch.testing.assert_close(
                    a["model"][key], b["model"][key], rtol=1e-4, atol=2e-6
                )

    def test_resume_matches_uninterrupted_training(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            root = Path(temporary)
            manifest = make_data(root)
            config = Config(
                model=ModelConfig(
                    mapping_width=4, channels=4, value_hidden=8, policy_hidden=4
                ),
                data=DataConfig(str(manifest), True, 5),
                run=RunConfig(
                    output=str(root / "whole"),
                    device="cpu",
                    steps=4,
                    batch_size=2,
                    warmup_steps=1,
                    validate_every=2,
                    validation_batches=2,
                    checkpoint_every=2,
                    log_every=1,
                    cpu_threads=2,
                ),
            )
            run_training(config)
            resumed = replace(
                config, run=replace(config.run, output=str(root / "resumed"))
            )
            self.assertFalse(run_training(resumed, stop_after=2)["completed"])
            self.assertTrue(run_training(resumed, resume=True)["completed"])
            a, b = (
                load_checkpoint(root / "whole/last.pt"),
                load_checkpoint(root / "resumed/last.pt"),
            )
            for key in a["model"]:
                torch.testing.assert_close(
                    a["model"][key], b["model"][key], rtol=0, atol=0
                )
            self.assertEqual(a["sampler"], b["sampler"])
            with self.assertRaisesRegex(ValueError, "differs"):
                run_training(
                    replace(resumed, run=replace(resumed.run, learning_rate=0.01)),
                    resume=True,
                )

    def test_initialize_preserves_weights_and_records_parent(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            root = Path(temporary)
            manifest = make_data(root)
            config = Config(
                model=ModelConfig(
                    mapping_width=4, channels=4, value_hidden=8, policy_hidden=4
                ),
                data=DataConfig(str(manifest)),
                run=RunConfig(
                    output=str(root / "parent"),
                    device="cpu",
                    steps=1,
                    batch_size=2,
                    validation_batches=1,
                    cpu_threads=2,
                ),
            )
            run_training(config)
            parent_path = root / "parent/last.pt"
            child = replace(config, run=replace(config.run, output=str(root / "child")))
            run_training(child, initialize=parent_path)
            parent, initial = (
                load_checkpoint(parent_path),
                load_checkpoint(root / "child/initial.pt"),
            )
            self.assertEqual(initial["step"], 0)
            self.assertEqual(
                initial["initializedFrom"]["checkpointSha256"], file_sha256(parent_path)
            )
            self.assertEqual(initial["initializedFrom"]["step"], 1)
            self.assertEqual(initial["optimizer"]["state"], {})
            for key in parent["model"]:
                torch.testing.assert_close(
                    parent["model"][key], initial["model"][key], rtol=0, atol=0
                )
            # A comparison must be able to evaluate old weights under the new loss.
            full = evaluate(parent_path, manifest, device="cpu")
            value_only = evaluate(
                parent_path,
                manifest,
                device="cpu",
                loss_config=replace(config.loss, policy_weight=0),
            )
            self.assertAlmostEqual(
                full["loss"] - value_only["loss"], full["policyLoss"], places=5
            )
            self.assertEqual(full["teacherScalarMse"], value_only["teacherScalarMse"])

    def test_quantized_export_matches_formal_model(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            root = Path(temporary)
            manifest = make_data(root)
            config = Config(
                model=ModelConfig(
                    mapping_width=4, channels=4, value_hidden=8, policy_hidden=4
                ),
                data=DataConfig(str(manifest)),
                run=RunConfig(
                    output=str(root / "run"),
                    device="cpu",
                    steps=1,
                    batch_size=2,
                    validation_batches=1,
                    cpu_threads=2,
                ),
            )
            run_training(config)
            state = load_checkpoint(root / "run/last.pt")
            model = LineNNUE(config.model).eval()
            model.load_state_dict(state["model"])
            exported = export_checkpoint(root / "run/last.pt", root / "export")
            self.assertFalse(exported["runtimeCompatible"])
            reference = QuantizedReference(root / "export/manifest.json")
            try:
                for sample in list(fixture_samples())[:3]:
                    row, board = pack_sample(sample)
                    batch = {
                        k: torch.from_numpy(v)
                        for k, v in decode_batch([row], 15).items()
                    }
                    with torch.no_grad():
                        expected = model(batch["boards"], batch["turn"])
                    actual = reference.predict(board, int(row["turn"]))
                    np.testing.assert_allclose(
                        actual["value"], expected["value"][0].numpy(), atol=2e-5, rtol=0
                    )
                    np.testing.assert_allclose(
                        actual["policy"],
                        expected["policy"][0].numpy(),
                        atol=2e-5,
                        rtol=0,
                    )
            finally:
                reference.close()
            parity, vectors = reference_check(
                root / "run/last.pt",
                root / "export/manifest.json",
                manifest,
                positions=3,
            )
            self.assertTrue(parity["passed"])
            self.assertEqual(len(vectors), parity["positions"])
            repository = Path(__file__).resolve().parents[2]
            checker = repository / "build/dev/bin" / (
                "gomoku-nnue-check.exe" if sys.platform == "win32" else "gomoku-nnue-check"
            )
            self.assertTrue(checker.is_file(), "Build the native engine before runtime integration tests")
            # Exercise both colors, corners/edges and uneven 20x20 pooling regions.
            for size, rule in ((15, "freestyle"), (20, "standard")):
                binary = root / "export/weights.gnn"
                raw = bytearray(binary.read_bytes())
                struct.pack_into("<II", raw, 40, size, 0 if rule == "freestyle" else 1)
                binary.write_bytes(raw)
                deployment = {**exported, "size": size, "rule": rule, "sha256": file_sha256(binary)}
                (root / "export/manifest.json").write_text(json.dumps(deployment), encoding="utf-8")
                quantized = QuantizedReference(root / "export/manifest.json")
                cases = []
                try:
                    rng = np.random.default_rng(94)
                    for turn in (1, 2):
                        for stones in (0, 1, 8, 24):
                            board = np.zeros((size, size), dtype=np.uint8)
                            points = rng.choice(size * size, stones, replace=False)
                            board.reshape(-1)[points] = np.arange(stones) % 2 + 1
                            if stones:
                                board[0, 0] = turn
                                board[-1, -1] = 3 - turn
                            prediction = quantized.predict(board, turn)
                            cases.append({"board": board.tolist(), "toMove": turn,
                                          "valueLogits": prediction["value"].tolist(),
                                          "policyLogits": prediction["policy"].tolist()})
                finally:
                    quantized.close()
                cases_path = root / "runtime-vectors.json"
                cases_path.write_text(json.dumps({"format": "line-nnue-reference-v1", "vectors": cases}), encoding="utf-8")
                for command in ([str(checker), str(binary), str(cases_path)],
                                ["node", str(repository / "scripts/check-export.mjs"), str(binary), str(cases_path)]):
                    subprocess.run(command, check=True, capture_output=True, text=True, timeout=60)
            with self.assertRaisesRegex(ValueError, "checkpoint"):
                reference_check(
                    root / "run/initial.pt", root / "export/manifest.json", manifest
                )
            path = root / "export/manifest.json"
            invalid = json.loads(path.read_text(encoding="utf-8"))
            invalid["architectureHash"] = "incorrect"
            path.write_text(json.dumps(invalid), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "architecture"):
                QuantizedReference(path)
            np.testing.assert_array_equal(
                round_divide(np.array([-5, -3, -1, 1, 3, 5]), 2), [-2, -2, 0, 0, 2, 2]
            )


if __name__ == "__main__":
    unittest.main()
