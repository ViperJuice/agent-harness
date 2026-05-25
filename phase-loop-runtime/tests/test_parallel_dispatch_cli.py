import os
import unittest
from unittest.mock import patch

from phase_loop_runtime.cli import build_parser
from phase_loop_runtime.pipeline_adapter.flag import parallel_dispatch_enabled


class ParallelDispatchCliTest(unittest.TestCase):
    def test_feature_flag_defaults_off(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(parallel_dispatch_enabled())

    def test_parallel_dispatch_option_hidden_when_feature_disabled(self):
        with patch.dict(os.environ, {"PHASE_LOOP_PARALLEL_DISPATCH": "false"}, clear=True):
            parser = build_parser()

        with self.assertRaises(SystemExit):
            parser.parse_args(["run", "--parallel-dispatch"])

    def test_parallel_dispatch_option_accepted_when_feature_enabled(self):
        with patch.dict(os.environ, {"PHASE_LOOP_PARALLEL_DISPATCH": "true"}, clear=True):
            parser = build_parser()

        args = parser.parse_args(["run", "--parallel-dispatch"])

        self.assertTrue(args.parallel_dispatch)


if __name__ == "__main__":
    unittest.main()
