import unittest
from xml.etree import ElementTree

from controlstudio_contracts.native_simulator_saved_source import parse_project_xml


class ProjectXmlParsingTests(unittest.TestCase):
    def test_encoding_aware_declaration_rejection_preserves_caller_error(self):
        class CallerError(ValueError):
            pass

        for encoding in ("utf-8", "utf-16", "utf-16-le", "utf-16-be"):
            for declaration in ('<!DOCTYPE Project>', '<!DOCTYPE Project [<!ENTITY x "x">]>'):
                with self.subTest(encoding=encoding, declaration=declaration):
                    xml = (declaration + '<Project/>').encode(encoding)
                    with self.assertRaisesRegex(CallerError, "^caller declaration error$") as caught:
                        parse_project_xml(xml, error_type=CallerError,
                                          declaration_error_message="caller declaration error")
                    self.assertIs(type(caught.exception), CallerError)
                    self.assertIsNone(caught.exception.__cause__)

    def test_byte_guard_and_parse_error_are_not_reclassified(self):
        with self.assertRaisesRegex(ValueError, "^declaration$"):
            parse_project_xml(b'<!-- <!ENTITY legacy --> <Project/>', error_type=ValueError,
                              declaration_error_message="declaration")
        with self.assertRaises(ElementTree.ParseError):
            parse_project_xml(b'<Project>', error_type=ValueError,
                              declaration_error_message="declaration")

    def test_normal_tree_matches_default_parser(self):
        for encoding in ("utf-8", "utf-16", "utf-16-le", "utf-16-be"):
            with self.subTest(encoding=encoding):
                xml = '<Project>정상<!--ignored--><?ignored a?><Child x="42"/></Project>'.encode(encoding)
                root = parse_project_xml(xml, error_type=ValueError,
                                         declaration_error_message="declaration")
                self.assertEqual(ElementTree.tostring(root),
                                 ElementTree.tostring(ElementTree.fromstring(xml)))
