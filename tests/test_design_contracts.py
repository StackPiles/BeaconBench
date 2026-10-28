"""Semantic regression checks for public design contracts, not model benchmarks."""
import copy
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('design_checks', ROOT / 'tools/check_design.py')
checks = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checks)


class Contracts(unittest.TestCase):
    def inputs(self):
        return [checks.read_json(ROOT / path) for path in ['catalog/benchmarks.json', 'suites/quick.v0.draft.json',
                'suites/detailed.v0.draft.json', 'protocols/common-ja.v0.draft.json']]

    def test_quick_cannot_add_an_output_or_action_budget(self):
        for field in checks.LIMIT_KEYS:
            args = self.inputs()
            args[1]['limits'][field] = 8192
            with self.assertRaisesRegex(ValueError, 'cap added'):
                checks.validate_blueprints(*args)

    def test_reuse_requires_shared_protocol_and_seed_prefix(self):
        for change in ['protocol', 'seed']:
            args = self.inputs()
            if change == 'protocol':
                args[1]['protocol_id'] = 'different'
            else:
                args[1]['trial_seeds'] = [999]
            with self.assertRaises(ValueError):
                checks.validate_blueprints(*args)

    def test_wrong_allocation_or_unverified_frozen_set_is_rejected(self):
        for change in ['count', 'ready', 'frozen']:
            args = self.inputs()
            if change == 'count':
                args[2]['allocation'][0]['cases'] += 1
            elif change == 'ready':
                args[1]['execution_ready'] = True
            else:
                args[1]['selection_frozen'] = True
            with self.assertRaises(ValueError):
                checks.validate_blueprints(*args)

    def test_reasoning_and_cached_tokens_are_not_added_twice(self):
        original = checks.read_json(ROOT / 'examples/measurement.fixture.json')
        checks.validate_measurement_semantics(original)
        for extra in [8, 40]:
            changed = copy.deepcopy(original)
            changed['usage']['total_tokens'] += extra
            with self.assertRaisesRegex(ValueError, 'double-counts'):
                checks.validate_measurement_semantics(changed)

    def test_missing_usage_stays_unknown(self):
        record = checks.read_json(ROOT / 'examples/measurement.fixture.json')
        record['usage'] = {key: None for key in record['usage']}
        checks.validate_measurement_semantics(record)
        self.assertTrue(all(value is None for value in record['usage'].values()))

    def test_reported_first_output_cannot_follow_completion(self):
        record = checks.read_json(ROOT / 'examples/measurement.fixture.json')
        record['times']['ttft_seconds'] = 2.0
        with self.assertRaisesRegex(ValueError, 'after request completion'):
            checks.validate_measurement_semantics(record)

    def test_secret_and_private_host_content_is_rejected_without_echoing_value(self):
        samples = ['gh' + 'p_' + 'a' * 36, '192.' + '168.9.11', 'https://user' + ':password@example.com',
                   '/' + 'home' + '/sample-user/private', 'C:' + '/Users/' + 'sample-user/private']
        for value in samples:
            with self.assertRaises(ValueError) as error:
                checks.check_no_private_content('docs/example.md', value)
            self.assertNotIn(value, str(error.exception))

    def test_normal_public_template_is_allowed(self):
        checks.check_no_private_content('examples/model.example.json', '${BEACON_MODEL_API_KEY}')
        checks.check_no_private_content('docs/example.md', 'http://127.0.0.1:8000/v1')

    def test_local_denylist_ignores_case_and_spacing_without_echoing_name(self):
        term = 'Confidential Cobalt Sample'
        for value in [term, 'confidential cobalt sample', 'CONFIDENTIAL\nCOBALT\tSAMPLE']:
            with self.assertRaisesRegex(ValueError, 'local denylist') as error:
                checks.check_no_private_content('docs/example.md', value, [term])
            self.assertNotIn(term, str(error.exception))
            self.assertNotIn(value, str(error.exception))
        checks.check_no_private_content('docs/example.md', 'A public example', [term])

    def test_nonfinite_usage_cannot_hide_in_json_numbers(self):
        for value in ['NaN', 'Infinity', '-Infinity', '1e999']:
            with self.assertRaises(ValueError):
                checks.parse_json('{"tokens":' + value + '}')


if __name__ == '__main__':
    unittest.main()
