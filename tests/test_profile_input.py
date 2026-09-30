import unittest

from promptbase_exporter.client import PromptBaseError, parse_profile_input


class ParseProfileInputTests(unittest.TestCase):
    def test_full_url(self):
        self.assertEqual(
            parse_profile_input("https://promptbase.com/profile/acb"),
            "acb",
        )

    def test_profile_path(self):
        self.assertEqual(parse_profile_input("profile/acb"), "acb")

    def test_username(self):
        self.assertEqual(parse_profile_input("acb"), "acb")

    def test_at_username(self):
        self.assertEqual(parse_profile_input("@acb"), "acb")

    def test_url_without_scheme(self):
        for value in (
            "promptbase.com/profile/acb",
            "www.promptbase.com/profile/acb/",
            "PromptBase.com/profile/@acb?via=share",
        ):
            with self.subTest(value=value):
                self.assertEqual(parse_profile_input(value), "acb")

    def test_url_without_scheme_still_requires_a_profile_path(self):
        with self.assertRaises(PromptBaseError):
            parse_profile_input("promptbase.com/prompt/some-slug")


if __name__ == "__main__":
    unittest.main()
