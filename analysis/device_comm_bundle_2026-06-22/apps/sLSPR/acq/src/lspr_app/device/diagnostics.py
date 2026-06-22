from __future__ import annotations

import json

from seabreeze.spectrometers import Spectrometer, list_devices


def main() -> int:
    devices = list_devices()
    print(f"Detected devices: {devices}")
    if not devices:
        print("No SeaBreeze-compatible spectrometers detected.")
        return 1

    spectrometer = Spectrometer(devices[0])
    try:
        wavelengths = spectrometer.wavelengths()
        spectrometer.integration_time_micros(100_000)
        intensities = spectrometer.intensities(
            correct_dark_counts=False,
            correct_nonlinearity=False,
        )
        summary = {
            "model": spectrometer.model,
            "serial_number": spectrometer.serial_number,
            "pixels": len(wavelengths),
            "first_wavelength_nm": float(wavelengths[0]),
            "last_wavelength_nm": float(wavelengths[-1]),
            "min_intensity": float(intensities.min()),
            "max_intensity": float(intensities.max()),
            "mean_intensity": float(intensities.mean()),
        }
        print(json.dumps(summary, indent=2))
    finally:
        spectrometer.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
