"""Golden cases for the raw-evidence comparison grammar, without vendor IO."""

import unittest

from controlstudio_contracts.native_ld_comparison import parse_comparison_expression


class NativeLdComparisonTests(unittest.TestCase):
    def test_signed_word_and_double_word_bounds(self):
        for instruction, minimum, maximum, width, data_type in (
            ("=", -32768, 32767, 1, "signed_16_bit"),
            ("D=", -2147483648, 2147483647, 2, "signed_32_bit"),
        ):
            with self.subTest(instruction=instruction):
                parsed = parse_comparison_expression(f"{instruction},{minimum},{maximum}")
                self.assertEqual(parsed, {
                    "instruction": instruction, "relation": "equal", "data_type": data_type,
                    "width_words": width, "lhs": {"kind": "literal", "value": minimum},
                    "rhs": {"kind": "literal", "value": maximum}, "devices": [],
                })
                self.assertIsNone(parse_comparison_expression(f"{instruction},{minimum - 1},0"))
                self.assertIsNone(parse_comparison_expression(f"{instruction},0,{maximum + 1}"))

    def test_operators_and_ordered_device_deduplication(self):
        for instruction, relation in (("=", "equal"), ("<>", "not_equal"),
                                      (">", "greater_than"), ("<", "less_than"),
                                      (">=", "greater_than_or_equal"),
                                      ("<=", "less_than_or_equal")):
            parsed = parse_comparison_expression(f" {instruction} , D2 , P1 ")
            self.assertEqual(parsed["relation"], relation)
            self.assertEqual(parsed["devices"], ["D2", "P1"])
        self.assertEqual(parse_comparison_expression("D>=,P1,P1")["devices"], ["P1"])
        self.assertEqual(parse_comparison_expression("=,+42,-0001")["rhs"],
                         {"kind": "literal", "value": -1})

    def test_unrepresented_syntax_stays_unrepresented(self):
        for expression in ("", "=", "=,P1", "=,P1,1,2", "d=,P1,1", "DNEG,P1,1",
                           "=,Y1,1", "=,d1,1", "=,P0A,1", "=,D0.1,1", "=,,1",
                           "=,P1,NaN", "=,P1,H10", "=,P1,True"):
            with self.subTest(expression=expression):
                self.assertIsNone(parse_comparison_expression(expression))

    def test_non_text_is_not_coerced(self):
        for value in (None, False, True):
            with self.subTest(value=value), self.assertRaises(AttributeError):
                parse_comparison_expression(value)


if __name__ == "__main__":
    unittest.main()
