"""Read-only preview arithmetic; no browser, model, or ledger required."""
import unittest

from benchmarks.attention_harness.dev_ui_server import budget_preview


class DevUiPreviewTest(unittest.TestCase):
    def test_eval_contributes_calls_tokens_and_model_time(self):
        selection = dict(tasks=['cube_lift'], seeds=[101], policies=['reactive_help'],
                         help_budget=2, target_depth=3, target_valid_executions=3,
                         max_versions=5, coverage='strict_full_depth',
                         eval_agent={'enabled': True})
        on = budget_preview(selection)['per_cell']
        off = budget_preview({**selection, 'eval_agent': {'enabled': False}})['per_cell']
        self.assertEqual(on['eval_calls'], 3)
        self.assertEqual(off['eval_calls'], 0)
        self.assertEqual(on['model_seconds'] - off['model_seconds'], 3 * 120)
        self.assertGreater(on['input_tokens'], off['input_tokens'])
        self.assertGreater(on['provider_tokens'], off['provider_tokens'])


if __name__ == '__main__':
    unittest.main()
