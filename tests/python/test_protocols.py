import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "src"))
sys.path.insert(0, str(ROOT / "training" / "src"))
from gomoku_tools.protocol import Worker, Pbrain, EngineProcess, binary
from gomoku_tools.selfplay import play
from gomoku_tools.arena import match
from gomoku_training.dataset import validate, examples, encode


class ProtocolTests(unittest.TestCase):
    def test_worker_and_competition_agree_on_tactics(self):
        fixture = json.loads((ROOT / "tests/fixtures/winning-move.json").read_text())
        position = fixture["position"]
        with Worker() as worker, Pbrain(binary("pbrain-gomoku"), time_ms=30) as brain:
            result = worker.request("analyze", position=position, limits={"timeMs": 30, "maxDepth": 2})
            self.assertEqual(result["bestMove"], fixture["bestMove"])
            self.assertEqual(brain.move(position["moves"]), fixture["bestMove"])
            result = worker.request("play", position=position, move=result["bestMove"])
            self.assertEqual(result["status"], fixture["statusAfterMove"])

    def test_invalid_worker_requests_do_not_kill_process(self):
        with Worker() as worker:
            for position in [
                {"size": 16, "rule": "freestyle", "moves": []},
                {"size": 15, "rule": "renju", "moves": []},
                {"size": 15, "rule": "freestyle", "moves": [{"x": 0.5, "y": 0}]},
                {"size": 15, "rule": "freestyle", "moves": [{"x": 0, "y": 0}] * 2},
            ]:
                with self.assertRaises(ValueError):
                    worker.request("inspect", position=position)
            self.assertEqual(worker.request("about")["protocolVersion"], 1)

    def test_worker_stop_and_subsequent_request(self):
        with EngineProcess(binary()) as process:
            position = {"size": 20, "rule": "freestyle", "moves": [{"x": 10, "y": 10}]}
            process.send(json.dumps({"v": 1, "id": "search", "method": "analyze", "position": position,
                                     "limits": {"timeMs": 10000, "maxDepth": 12}}))
            process.send(json.dumps({"v": 1, "id": "stop", "method": "stop", "targetId": "search"}))
            replies = [json.loads(process.receive()), json.loads(process.receive())]
            results = {reply["id"]: reply["result"] for reply in replies}
            self.assertTrue(results["stop"]["matched"])
            self.assertEqual(results["search"]["reason"], "cancelled")
            process.send(json.dumps({"v": 1, "id": "about", "method": "about"}))
            self.assertTrue(json.loads(process.receive())["ok"])

    def test_gomocup_info_start_restart_and_unknown(self):
        commands = "\n".join([
            "INFO timeout_turn 0", "INFO rule 1", "START 15", "BEGIN",
            "RESTART", "ABOUT", "NOT_A_COMMAND", "END", "",
        ])
        result = subprocess.run([str(binary("pbrain-gomoku"))], input=commands,
                                capture_output=True, text=True, timeout=5, check=True)
        lines = result.stdout.splitlines()
        self.assertEqual(lines[0], "OK")
        self.assertIn(",", lines[1])
        self.assertEqual(lines[2], "OK")
        self.assertTrue(lines[3].startswith('name="'))
        self.assertEqual(lines[4], "UNKNOWN NOT_A_COMMAND")
        self.assertEqual(len(lines), 5)

    def test_gomocup_rejects_unsupported_rule_after_info(self):
        commands = "START 15\nINFO rule 4\nBEGIN\nEND\n"
        result = subprocess.run([str(binary("pbrain-gomoku"))], input=commands,
                                capture_output=True, text=True, timeout=5, check=True)
        lines = result.stdout.splitlines()
        self.assertEqual(lines[0], "OK")
        self.assertTrue(lines[1].startswith("ERROR Unsupported rule"))
        self.assertEqual(len(lines), 2)

    def test_board_blocks_recover_after_invalid_row(self):
        commands = "START 15\nBOARD\ninvalid\nDONE\nABOUT\nEND\n"
        result = subprocess.run([str(binary("pbrain-gomoku"))], input=commands,
                                capture_output=True, text=True, timeout=5, check=True)
        self.assertTrue(result.stdout.splitlines()[1].startswith("ERROR"))
        self.assertTrue(result.stdout.splitlines()[2].startswith('name="'))

    def test_selfplay_and_training_data_complete_roundtrip(self):
        with Worker() as worker:
            record = play(worker, time_ms=0, depth=1)
            validate(record, worker)
        self.assertNotEqual(record["status"], "playing")
        data = list(examples(record))
        self.assertEqual(len(data), len(record["moves"]))
        self.assertEqual(len(data[0][0]), 450)
        if record["status"] != "draw":
            self.assertEqual(data[0][1], -data[1][1])

    def test_feature_planes_are_side_to_move_relative(self):
        features = encode(15, [{"x": 7, "y": 7}])
        self.assertEqual(features[225 + 7 * 15 + 7], 1)
        self.assertEqual(sum(features), 1)

    def test_paired_competition_arena(self):
        results = match(binary("pbrain-gomoku"), binary("pbrain-gomoku"), time_ms=0)
        self.assertEqual(len(results), 2)
        self.assertEqual([game["aColor"] for game in results], ["black", "white"])
        self.assertTrue(all(game["failure"] is None for game in results))
        self.assertEqual(sum(game["scoreA"] for game in results), 1)


if __name__ == "__main__":
    unittest.main()
