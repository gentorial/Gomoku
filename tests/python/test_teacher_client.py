import unittest
from gomoku_tools.teacher_client import parse_iterations, parse_score
from gomoku_tools.mine import choose_positions
from gomoku_tools.arena import paired_summary


def pv(depth, index, score, move, count=2):
    return [f"INFO PV {index}", f"INFO NUMPV {count}", f"INFO DEPTH {depth}",
            "INFO TOTALNODES 100", f"INFO EVAL {score}", f"INFO BESTLINE {move}", "INFO PV DONE"]


class TeacherClientTests(unittest.TestCase):
    def test_interrupted_iteration_is_not_mixed_with_completed_alternatives(self):
        lines = pv(8, 0, 40, "7,7 7,8") + pv(8, 1, 30, "8,7") + pv(9, 0, 80, "8,7")
        result = parse_iterations(lines, 15)
        self.assertEqual([p["eval"] for p in result], [40, 30])
        self.assertEqual([p["depth"] for p in result], [8, 8])
        self.assertEqual(result[0]["move"], {"x": 7, "y": 7})

    def test_mate_protocol_preserves_sign_and_distance(self):
        self.assertEqual(parse_score("+M5"), 29995)
        self.assertEqual(parse_score("-M6"), -29994)
        self.assertEqual(parse_score("-M*"), -29500)
        self.assertEqual(parse_score("-820"), -820)
        with self.assertRaises(ValueError):
            parse_score("VAL_INF")

    def test_bad_or_duplicate_pv_rejected(self):
        with self.assertRaises(ValueError):
            parse_iterations(pv(5, 0, 5, "15,0", 1), 15)
        with self.assertRaises(ValueError):
            parse_iterations(pv(5, 0, 5, "2,2") + pv(5, 1, 4, "2,2"), 15)

    def test_search_variation_does_not_inherit_real_game_outcome(self):
        record = {"size": 15, "rule": "freestyle", "status": "black_win",
                  "moves": [{"x": 7, "y": 7}, {"x": 7, "y": 8}, {"x": 8, "y": 8}],
                  "analyses": [{"ply": 1, "score": {"value": 100},
                                "pv": [{"x": 6, "y": 7}, {"x": 8, "y": 7}]}]}
        selected = choose_positions(record, 1, 1)
        self.assertEqual([s["kind"] for s in selected], ["root", "pv_leaf"])
        self.assertEqual(selected[0]["result"], "black_win")
        self.assertIsNone(selected[1]["result"])

    def test_sign_test_counts_pairs_not_correlated_games(self):
        games = [{"openingId": str(i), "aColor": c, "scoreA": 1}
                 for i in range(5) for c in ("black", "white")]
        stats = paired_summary(games)
        self.assertEqual(stats["pairs"], 5)
        self.assertEqual(stats["oneSidedSignP"], 1 / 32)
        with self.assertRaises(ValueError):
            paired_summary(games + games[:1])


if __name__ == "__main__":
    unittest.main()
