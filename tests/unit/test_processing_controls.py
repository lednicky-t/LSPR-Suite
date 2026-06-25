from __future__ import annotations

import unittest

from tests._paths import ensure_repo_paths


ensure_repo_paths()

from lspr_app.gui.main_window_processing import (
    ANALYSIS_RESOLUTION_OPTIONS,
    analysis_resolution_value,
    populate_analysis_resolution_combo,
    set_analysis_resolution_value,
)


class _FakeCombo:
    def __init__(self) -> None:
        self.items: list[tuple[str, object]] = []
        self.index = 0

    def clear(self) -> None:
        self.items.clear()
        self.index = 0

    def addItem(self, label: str, value: object) -> None:
        self.items.append((label, value))

    def currentData(self) -> object:
        return self.items[self.index][1]

    def currentText(self) -> str:
        return self.items[self.index][0]

    def findData(self, value: object) -> int:
        for index, (_, item_value) in enumerate(self.items):
            if item_value == value:
                return index
        return -1

    def findText(self, text: str) -> int:
        for index, (item_text, _) in enumerate(self.items):
            if item_text == text:
                return index
        return -1

    def setCurrentIndex(self, index: int) -> None:
        self.index = index


class ProcessingControlsTests(unittest.TestCase):
    def test_analysis_resolution_combo_uses_expected_labels_and_values(self) -> None:
        combo = _FakeCombo()

        populate_analysis_resolution_combo(combo)

        self.assertEqual(combo.items, list(ANALYSIS_RESOLUTION_OPTIONS))

    def test_analysis_resolution_combo_round_trips_values(self) -> None:
        combo = _FakeCombo()
        populate_analysis_resolution_combo(combo)

        set_analysis_resolution_value(combo, 0.00001)

        self.assertEqual(combo.currentData(), 0.00001)
        self.assertEqual(analysis_resolution_value(combo), 0.00001)
