"""Coverage for domain/exclusions.py - no test file existed for it before this.

ImageExclusionRule lets a user drop a bad frame (a specific cube+wavelength),
a bad wavelength across every cube, or a whole bad cube from analysis. Getting
the wildcard/scope-matching logic wrong here means either a bad frame silently
stays in a fit (wrong result) or a good frame is silently dropped (lost data)
- both are correctness issues, not cosmetic ones.
"""

from __future__ import annotations

import sys
import unittest

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_imaging_app.domain.exclusions import (
    ImageExclusionRule,
    is_cube_fully_excluded,
    is_excluded,
    remove_rule,
    rule_matches,
    upsert_rule,
)


class TestRuleMatches(unittest.TestCase):
    def test_exact_scope_matches_only_that_cube_and_wavelength(self) -> None:
        rule = ImageExclusionRule(spectral_cube_index=2, wavelength_nm=550.0)
        self.assertTrue(rule_matches(rule, spectral_cube_index=2, wavelength_nm=550.0))
        self.assertFalse(rule_matches(rule, spectral_cube_index=3, wavelength_nm=550.0))
        self.assertFalse(rule_matches(rule, spectral_cube_index=2, wavelength_nm=560.0))

    def test_wildcard_cube_matches_that_wavelength_in_any_cube(self) -> None:
        rule = ImageExclusionRule(spectral_cube_index=None, wavelength_nm=550.0)
        self.assertTrue(rule_matches(rule, spectral_cube_index=0, wavelength_nm=550.0))
        self.assertTrue(rule_matches(rule, spectral_cube_index=99, wavelength_nm=550.0))
        self.assertFalse(rule_matches(rule, spectral_cube_index=0, wavelength_nm=560.0))

    def test_wildcard_wavelength_matches_the_whole_cube(self) -> None:
        rule = ImageExclusionRule(spectral_cube_index=4, wavelength_nm=None)
        self.assertTrue(rule_matches(rule, spectral_cube_index=4, wavelength_nm=400.0))
        self.assertTrue(rule_matches(rule, spectral_cube_index=4, wavelength_nm=900.0))
        self.assertFalse(rule_matches(rule, spectral_cube_index=5, wavelength_nm=400.0))

    def test_both_fields_wildcard_matches_everything(self) -> None:
        rule = ImageExclusionRule(spectral_cube_index=None, wavelength_nm=None)
        self.assertTrue(rule_matches(rule, spectral_cube_index=0, wavelength_nm=400.0))
        self.assertTrue(rule_matches(rule, spectral_cube_index=999, wavelength_nm=999.0))

    def test_matching_tolerates_int_float_type_mismatch(self) -> None:
        # Cube indices and wavelengths can arrive as numpy scalar types from
        # dataset metadata rather than plain int/float - the int()/float()
        # coercion in rule_matches must not make an equal value miss.
        rule = ImageExclusionRule(spectral_cube_index=2, wavelength_nm=550.0)
        self.assertTrue(rule_matches(rule, spectral_cube_index=2.0, wavelength_nm=550))


class TestIsExcluded(unittest.TestCase):
    def test_empty_rules_excludes_nothing(self) -> None:
        self.assertFalse(is_excluded([], spectral_cube_index=0, wavelength_nm=500.0))

    def test_true_when_any_rule_matches(self) -> None:
        rules = [
            ImageExclusionRule(spectral_cube_index=0, wavelength_nm=400.0),
            ImageExclusionRule(spectral_cube_index=1, wavelength_nm=500.0),
        ]
        self.assertTrue(is_excluded(rules, spectral_cube_index=1, wavelength_nm=500.0))
        self.assertFalse(is_excluded(rules, spectral_cube_index=1, wavelength_nm=400.0))


class TestIsCubeFullyExcluded(unittest.TestCase):
    def test_specific_wavelength_rule_does_not_fully_exclude_the_cube(self) -> None:
        rules = [ImageExclusionRule(spectral_cube_index=3, wavelength_nm=550.0)]
        self.assertFalse(is_cube_fully_excluded(rules, spectral_cube_index=3))

    def test_whole_cube_rule_fully_excludes_only_that_cube(self) -> None:
        rules = [ImageExclusionRule(spectral_cube_index=3, wavelength_nm=None)]
        self.assertTrue(is_cube_fully_excluded(rules, spectral_cube_index=3))
        self.assertFalse(is_cube_fully_excluded(rules, spectral_cube_index=4))

    def test_global_wildcard_rule_fully_excludes_every_cube(self) -> None:
        rules = [ImageExclusionRule(spectral_cube_index=None, wavelength_nm=None)]
        self.assertTrue(is_cube_fully_excluded(rules, spectral_cube_index=0))
        self.assertTrue(is_cube_fully_excluded(rules, spectral_cube_index=123))

    def test_empty_rules_fully_excludes_nothing(self) -> None:
        self.assertFalse(is_cube_fully_excluded([], spectral_cube_index=0))


class TestUpsertRule(unittest.TestCase):
    def test_new_scope_is_appended(self) -> None:
        rules: list[ImageExclusionRule] = []
        upsert_rule(rules, 1, 500.0, note="bad frame")
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0].spectral_cube_index, 1)
        self.assertEqual(rules[0].wavelength_nm, 500.0)
        self.assertEqual(rules[0].note, "bad frame")

    def test_same_scope_replaces_the_existing_rule_instead_of_duplicating(self) -> None:
        rules: list[ImageExclusionRule] = []
        upsert_rule(rules, 1, 500.0, note="first note")
        upsert_rule(rules, 1, 500.0, note="updated note")
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0].note, "updated note")

    def test_different_scopes_both_persist(self) -> None:
        rules: list[ImageExclusionRule] = []
        upsert_rule(rules, 1, 500.0)
        upsert_rule(rules, 1, None)  # whole-cube rule for cube 1 - a distinct scope
        upsert_rule(rules, None, 500.0)  # wavelength-wildcard rule - also distinct
        self.assertEqual(len(rules), 3)


class TestRemoveRule(unittest.TestCase):
    def test_removes_only_the_exact_scope_match(self) -> None:
        rules = [
            ImageExclusionRule(spectral_cube_index=1, wavelength_nm=500.0),
            ImageExclusionRule(spectral_cube_index=1, wavelength_nm=600.0),
        ]
        remove_rule(rules, 1, 500.0)
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0].wavelength_nm, 600.0)

    def test_removing_a_scope_with_no_matching_rule_is_a_no_op(self) -> None:
        rules = [ImageExclusionRule(spectral_cube_index=1, wavelength_nm=500.0)]
        remove_rule(rules, 2, 500.0)
        self.assertEqual(len(rules), 1)


if __name__ == "__main__":
    unittest.main()
