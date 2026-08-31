from __future__ import annotations

import copy
import unittest

from controlstudio_host.native_projects import (
    NativeProjectPreconditionError,
    _canonical_native_io_address,
    _compile_native_program,
)
from controlstudio_host.store import _native_ladder_is_executable


def _seal_in_artifact() -> dict[str, object]:
    def confirmed() -> dict[str, object]:
        return {
            "status": "confirmed",
            "reason": "Offline seal-in fixture.",
            "evidence": ["Test fixture."],
            "basis": None,
        }

    def not_applicable(basis: str) -> dict[str, object]:
        return {
            "status": "not_applicable",
            "reason": "Offline seal-in fixture has no optional behavior.",
            "evidence": ["Test fixture."],
            "basis": basis,
        }

    return {
        "technicalConfiguration": {
            "plcSeries": "XGK",
            "cpuModel": "XGK-CPUH",
            "programName": "MainProgram",
        },
        "design": {
            "schemaVersion": "2",
            "collectionScope": {
                "ioPoints": confirmed(),
                "operatingModes": not_applicable("single_mode_operation"),
                "controlNetworks": confirmed(),
                "timers": not_applicable("no_timing_behavior"),
                "sequences": not_applicable("no_sequence_behavior"),
                "alarms": not_applicable("no_alarm_behavior"),
                "recoveryRules": not_applicable("no_recovery_behavior"),
                "safetyRules": not_applicable("no_physical_or_hazard_outputs"),
            },
            "ioPoints": [
                {
                    "signalId": "start",
                    "symbol": "START",
                    "address": "M0000",
                    "kind": "internal_bit",
                    "normalState": "off",
                    "safeState": "off",
                },
                {
                    "signalId": "stop_ok",
                    "symbol": "STOP_OK",
                    "address": "M0001",
                    "kind": "internal_bit",
                    "normalState": "on",
                    "safeState": "off",
                },
                {
                    "signalId": "run_cmd",
                    "symbol": "RUN_CMD",
                    "address": "M0002",
                    "kind": "internal_bit",
                    "normalState": "off",
                    "safeState": "off",
                },
            ],
            "operatingModes": [],
            "controlNetworks": [
                {
                    "networkId": "seal_in",
                    "title": "Internal seal-in network",
                    "modeId": None,
                    "stepId": None,
                    "permitAll": [{"signalId": "stop_ok", "state": "on"}],
                    "outputSignalId": "run_cmd",
                }
            ],
            "ladderRungs": [
                {
                    "rungId": "seal_in",
                    "description": "Latch RUN_CMD while STOP_OK is on.",
                    "condition": {
                        "kind": "all_of",
                        "terms": [
                            {
                                "kind": "any_of",
                                "terms": [
                                    {
                                        "kind": "contact",
                                        "sourceType": "signal",
                                        "sourceId": "start",
                                        "state": "on",
                                    },
                                    {
                                        "kind": "contact",
                                        "sourceType": "signal",
                                        "sourceId": "run_cmd",
                                        "state": "on",
                                    },
                                ],
                            },
                            {
                                "kind": "contact",
                                "sourceType": "signal",
                                "sourceId": "stop_ok",
                                "state": "on",
                            },
                        ],
                    },
                    "action": {
                        "instruction": "OUT",
                        "targetType": "signal",
                        "targetId": "run_cmd",
                    },
                }
            ],
            "timers": [],
            "sequences": [],
            "alarms": [],
            "recoveryRules": [],
            "safetyRules": [],
        },
    }


class XgkSealInCompileTests(unittest.TestCase):
    def test_compact_internal_operands_lower_to_native_xgk_source(self) -> None:
        artifact = _seal_in_artifact()

        compiled = _compile_native_program(
            artifact,
            program_name="MainProgram",
            plc_series="XGK",
        )

        self.assertEqual(
            compiled["source"],
            "LOAD M00000\nOR M00002\nAND M00001\nOUT M00002\nEND",
        )
        self.assertTrue(_native_ladder_is_executable(artifact))

    def test_compact_and_native_width_aliases_cannot_share_one_operand(self) -> None:
        artifact = _seal_in_artifact()
        design = artifact["design"]
        assert isinstance(design, dict)
        io_points = design["ioPoints"]
        assert isinstance(io_points, list)
        alias = copy.deepcopy(io_points[0])
        assert isinstance(alias, dict)
        alias.update(
            {
                "signalId": "start_alias",
                "symbol": "START_ALIAS",
                "address": "M00000",
            }
        )
        io_points.append(alias)

        with self.assertRaisesRegex(
            NativeProjectPreconditionError,
            "native I/O signal, symbol, and address values must be unique",
        ):
            _compile_native_program(
                artifact,
                program_name="MainProgram",
                plc_series="XGK",
            )

    def test_native_xgk_normalization_preserves_word_and_bit(self) -> None:
        self.assertEqual(_canonical_native_io_address("M002A", "XGK"), "M0002A")
        self.assertEqual(_canonical_native_io_address("P0021", "XGK"), "P00021")


if __name__ == "__main__":
    unittest.main()
