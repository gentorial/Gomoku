import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "src"))
from gomoku_tools.protocol import Worker


class LoggingTests(unittest.TestCase):
    def test_worker_logs_preserve_protocol_and_distinguish_limits(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            "GOMOKU_LOG_DIR": directory, "GOMOKU_LOG_LEVEL": "debug"
        }):
            with Worker() as worker:
                result = worker.request("analyze", request_id="log-node-limit",
                    position={"size": 15, "rule": "freestyle", "moves": [{"x": 7, "y": 7}]},
                    limits={"timeMs": 10000, "maxDepth": 8, "maxNodes": 20})
                self.assertEqual(result["reason"], "limit")
                self.assertIsNotNone(result["bestMove"])
                self.assertEqual(worker.request("about")["protocolVersion"], 1)
            records = [json.loads(line) for path in Path(directory).glob("engine-*.jsonl")
                       for line in path.read_text(encoding="utf-8").splitlines()]
            search = [record for record in records if record.get("requestId") == "log-node-limit"]
            self.assertTrue(any(record["event"] == "search.start" for record in search))
            end = next(record for record in search if record["event"] == "search.end")
            self.assertEqual(end["reason"], "node_limit")
            self.assertLessEqual(end["nodes"], 20)
            start = next(record for record in search if record["event"] == "search.start")
            self.assertEqual(start["limits"]["maxNodes"], 20)
            self.assertEqual(start["position"]["moves"], [{"x": 7, "y": 7}])
            self.assertIn("rootHash", start)
            self.assertTrue(any(record["event"] == "search.candidates" for record in search))
            self.assertTrue(all(record["component"] == "engine" for record in records))

    def test_off_creates_no_log_files(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            "GOMOKU_LOG_DIR": directory, "GOMOKU_LOG_LEVEL": "off"
        }):
            with Worker() as worker:
                self.assertEqual(worker.request("about")["protocolVersion"], 1)
            self.assertEqual(list(Path(directory).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
