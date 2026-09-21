from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/"tools/src"))
from gomoku_tools.teacher_data import multipv_policy, sampled_indices
from gomoku_tools.rapfi import Generation, command


class TeacherPolicyTests(unittest.TestCase):
    def test_close_moves_get_similar_targets_and_missing_scores_are_excluded(self):
        moves=[{"move":{"x":i,"y":2},"eval":score} for i,score in enumerate([100,99,-500,None])]
        p=multipv_policy(moves)
        self.assertEqual(p['kind'],'derived_distribution')
        self.assertEqual(len(p['moves']),3)
        self.assertAlmostEqual(sum(m['probability'] for m in p['moves']),1)
        self.assertLess(abs(p['moves'][0]['probability']-p['moves'][1]['probability']),.01)
        self.assertLess(p['moves'][2]['probability'],p['moves'][0]['probability'])

    def test_proven_mates_do_not_turn_into_ordinary_score_softmax(self):
        p=multipv_policy([{"move":{"x":i,"y":2},"eval":s} for i,s in enumerate([29995,29991,150])])
        self.assertEqual([m['probability'] for m in p['moves']],[.5,.5])

    def test_sampling_cap_covers_late_and_early_positions(self):
        game={'turns':[[{'eval':0}]]*150}
        selected=sampled_indices(game,64)
        self.assertEqual(len(set(selected)),64)
        self.assertEqual((selected[0],selected[-1]),(0,149))

    def test_generation_exposes_multipv_and_truncation_explicitly(self):
        config=Generation(multipv=4,max_plies=96,samples_per_game=64)
        config.validate()
        args=command(Path('rapfi'),config,Path('test.binpack'))
        self.assertEqual(args[args.index('--multipv')+1],'4')
        self.assertEqual(args[args.index('--force-draw-ply')+1],'96')


if __name__ == '__main__':
    unittest.main()
