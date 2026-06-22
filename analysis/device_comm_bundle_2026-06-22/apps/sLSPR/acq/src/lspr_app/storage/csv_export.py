from __future__ import annotations

from pathlib import Path

from lspr_app.domain.models import Spectrum
from lspr_io import write_xy_csv


def export_spectrum_to_csv(path: Path, spectrum: Spectrum) -> None:
    metadata = {"acquired_at": spectrum.acquired_at.isoformat(), **dict(spectrum.metadata)}
    write_xy_csv(
        path,
        x_label="wavelength_nm",
        x_values=spectrum.wavelengths_nm,
        y_label="value",
        y_values=spectrum.values,
        metadata=metadata,
    )
