import unittest

from cli.security import _eval_condition


class ImageTagPolicyTest(unittest.TestCase):
    """
    Regression test for a fixed inversion bug in _eval_condition's
    imageTagPolicy handling. require-digest and require-tag previously
    suppressed the flag for the risky case (missing digest/tag) and
    flagged the safe case instead.
    """

    def test_require_digest_flags_image_missing_a_digest(self):
        matched = _eval_condition(
            path="spec.modules[0].config.image",
            key="image",
            value="postgres:16",
            cond={"imageTagPolicy": "require-digest"},
            profile_class="prod",
        )
        self.assertTrue(matched, "an image with no digest should be flagged")

    def test_require_digest_does_not_flag_image_with_a_digest(self):
        matched = _eval_condition(
            path="spec.modules[0].config.image",
            key="image",
            value="postgres@sha256:" + "a" * 64,
            cond={"imageTagPolicy": "require-digest"},
            profile_class="prod",
        )
        self.assertFalse(matched, "a digest-pinned image should not be flagged")

    def test_require_tag_flags_image_missing_a_tag(self):
        matched = _eval_condition(
            path="spec.modules[0].config.image",
            key="image",
            value="postgres",
            cond={"imageTagPolicy": "require-tag"},
            profile_class="prod",
        )
        self.assertTrue(matched, "an image with no tag or digest should be flagged")

    def test_require_tag_does_not_flag_image_with_an_explicit_tag(self):
        matched = _eval_condition(
            path="spec.modules[0].config.image",
            key="image",
            value="postgres:16",
            cond={"imageTagPolicy": "require-tag"},
            profile_class="prod",
        )
        self.assertFalse(matched, "an explicitly tagged image should not be flagged")

    def test_require_tag_does_not_flag_digest_pinned_image(self):
        matched = _eval_condition(
            path="spec.modules[0].config.image",
            key="image",
            value="postgres@sha256:" + "a" * 64,
            cond={"imageTagPolicy": "require-tag"},
            profile_class="prod",
        )
        self.assertFalse(matched, "digest pinning satisfies require-tag's intent too")

    def test_forbid_latest_still_flags_the_latest_tag(self):
        # Control: forbid-latest was never inverted. Confirms the fix to
        # the other two branches didn't regress this one.
        matched = _eval_condition(
            path="spec.modules[0].config.image",
            key="image",
            value="postgres:latest",
            cond={"imageTagPolicy": "forbid-latest"},
            profile_class="prod",
        )
        self.assertTrue(matched, "an image using :latest should be flagged")

    def test_forbid_latest_still_ignores_explicit_tags(self):
        matched = _eval_condition(
            path="spec.modules[0].config.image",
            key="image",
            value="postgres:16",
            cond={"imageTagPolicy": "forbid-latest"},
            profile_class="prod",
        )
        self.assertFalse(matched, "an explicitly tagged image should not be flagged")


if __name__ == "__main__":
    unittest.main()
