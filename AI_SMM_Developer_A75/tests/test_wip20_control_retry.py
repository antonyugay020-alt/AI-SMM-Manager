import unittest
from artifact_review import validated_review_call, SubstanceControlFailure


class BlindControlRetryTests(unittest.TestCase):
    def test_transient_control_failure_retries_once(self):
        calls = []
        def ask(*args, **kwargs):
            calls.append((args, kwargs))
            return {'ok': True}
        def validate(value):
            if len(calls) == 1:
                raise SubstanceControlFailure('control mismatch')
            return value
        result = validated_review_call(ask, 'reviewer', 'system', 'unchanged document', {}, validate)
        self.assertEqual(result, {'ok': True})
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][0][2], calls[1][0][2])

    def test_persistent_failure_remains_blocking(self):
        calls = []
        def ask(*args, **kwargs):
            calls.append(1)
            return {'ok': True}
        def validate(value):
            raise SubstanceControlFailure('control mismatch')
        with self.assertRaises(SubstanceControlFailure):
            validated_review_call(ask, 'reviewer', 'system', 'unchanged document', {}, validate)
        self.assertEqual(len(calls), 2)


if __name__ == '__main__':
    unittest.main()
