import copy
import unittest

from controlstudio_host.mne_change_execution import _mne_xgsim_hold_verified


class MneHoldReceiptTests(unittest.TestCase):
    def test_new_hold_requires_matching_duration_and_repeated_samples(self):
        profile = {"scenario": {"steps": [{"hold_ms": 100}]}}
        observation = {"holdMs": 100, "holdObservedMs": 110, "holdSamples": 3,
                       "observationKind": "polled_steady_state"}
        self.assertTrue(_mne_xgsim_hold_verified(profile, [observation]))
        for key, value in (("holdMs", 0), ("holdObservedMs", 99),
                           ("holdObservedMs", float("nan")), ("holdObservedMs", float("inf")),
                           ("holdSamples", 1), ("holdSamples", True),
                           ("observationKind", "pulse_capture")):
            changed = dict(observation, **{key: value})
            with self.subTest(key=key, value=value):
                self.assertFalse(_mne_xgsim_hold_verified(profile, [changed]))
        self.assertFalse(_mne_xgsim_hold_verified(profile, [{"matched": True}]))

    def test_invalid_or_incomplete_profiles_do_not_pass(self):
        for hold in (True, -1, "100", None, 10001):
            self.assertFalse(_mne_xgsim_hold_verified({"scenario": {"steps": [{"hold_ms": hold}]}}, [{}]))
        self.assertFalse(_mne_xgsim_hold_verified({"scenario": {"steps": [{}]}}, []))

    def test_legacy_untimed_observations_remain_compatible(self):
        profile = {"scenario": {"steps": [{}]}}
        original = copy.deepcopy(profile)
        self.assertTrue(_mne_xgsim_hold_verified(profile, [{"matched": True}]))
        self.assertEqual(profile, original)


if __name__ == "__main__":
    unittest.main()
