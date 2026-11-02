# Notice

LSPR Suite - Copyright (c) 2026 Tomáš Lednický.

The Suite and its apps are released under the **MIT License** (see [`LICENSE`](LICENSE)). This applies to all the repositories that make up
the Suite: [LSPR-Suite](https://github.com/lednicky-t/LSPR-Suite), [SingleSpotLSPR-Acquisition](https://github.com/lednicky-t/SingleSpotLSPR-Acquisition),
[SingleSpotLSPR-Evaluation](https://github.com/lednicky-t/SingleSpotLSPR-Evaluation), [LSPRimaging-Acquisition](https://github.com/lednicky-t/LSPRimaging-Acquisition)
and [LSPRimaging-Evaluation](https://github.com/lednicky-t/LSPRimaging-Evaluation). Third-party software it uses has its own licences (last section).

## Use, also commercial

You may use, copy, modify and distribute the Suite, also in a company and also for profit. The one thing the licence requires is that the
copyright notice and the MIT permission notice stay with every copy or substantial part you distribute. Your data stay yours: the licence
claims no rights to the measurements, results or files you create with the software.

## Acknowledgement (a request)

If you use the Suite to acquire or evaluate data that appear in a publication, thesis, poster or report, please

- cite it (see [`CITATION.cff`](CITATION.cff), or the "Cite this repository" button on the GitHub page), and
- name it in the acknowledgements or the methods, with the version you used.

This is a request that rests on scientific practice, not a condition of the licence: the licence stays the plain MIT text.

## Disclaimer for research use

- The software is provided "as is", without warranty of any kind (full text in [`LICENSE`](LICENSE)). The author is not liable for loss or
  corruption of data, for wrong results or conclusions, or for any other damage arising from the use of the software.
- It is research software. Results depend on your sample, instrument and settings. Check the analysis on a measurement with a known answer
  before you rely on it, check fits and masks by eye, and keep your own backups of raw data and of the files the apps write. The apps are
  designed to leave raw measurement data untouched and to write derived results to separate files, but no software can promise that nothing
  is ever lost.
- It has not been validated for diagnostic, clinical or safety-critical use. This is a warning about its state of validation, not a restriction:
  the licence stays MIT.

## Names

The names "LSPR Suite" and the names of its apps identify this project; the licence does not grant rights to use them as trademarks. If you
distribute a modified version, please mark it as modified and do not present it as the original or imply that the author endorses it. This
protects the reputation of results that were produced with the original.

## Third-party software

The Suite is built on open-source packages. The full licence text of every package installed in the portable bundle is in the bundle's
`THIRD_PARTY_LICENSES` folder; it is generated when the bundle is built from what is really installed, so it is the authoritative list. The
summary below is of 2026-11-02 and may lag behind.

### PyQt6 (GPL v3)

The graphical interface uses PyQt6, (c) Riverbank Computing Limited, licensed under the GNU General Public License v3 (a commercial licence
is available from Riverbank). The Suite's own code is MIT and does not contain PyQt6 code. Using the Suite is not restricted by this, also in a
company. If you **redistribute** the Suite together with PyQt6 (for example the portable bundle, or a product built on it), the combination must
comply with PyQt6's licence: under the GPL v3, including making the source available, or with a commercial PyQt licence, or after porting to
another Qt binding. The source of PyQt6 is available at <https://pypi.org/project/PyQt6/> and <https://www.riverbankcomputing.com/software/pyqt/>.

### Other components with a notice requirement

- **crc32c** (LGPL v2.1 or later): installed as a separate package that can be replaced (`pip install --upgrade crc32c`); the apps call it and work
  without it.
- **certifi** (MPL 2.0): used unmodified.
- **opencv-python-headless** (Apache 2.0): its wheel contains further libraries with their own licences; the texts are in `THIRD_PARTY_LICENSES`.

### AMFTools (proprietary, not MIT)

AMFTools is proprietary software of Advanced Microfluidics SA (<https://amf.ch>). It is **not** covered by this project's MIT licence. It is
optional (M-Switch control). Its licence allows redistribution if its text is reproduced, and limits its use to AMF products; the text is in
`THIRD_PARTY_LICENSES`.

### Tabler Icons (MIT)

The icons in `packages/lspr_ui/src/lspr_ui/icon_assets/` are from [Tabler Icons](https://tabler.io/icons):

```
MIT License

Copyright (c) 2020-2026 Paweł Kuna

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

### Permissive packages (BSD, MIT, Apache, PSF)

pyqtgraph, numpy, scipy, h5py, pydantic, Pillow, imagecodecs, ome-zarr, numcodecs, zarr, scikit-image, tifffile, matplotlib, pyserial, PyYAML,
python-seabreeze, platformdirs, psutil, pywin32, PyQt6-sip, and pip with its vendored libraries. Each requires its copyright notice to be kept
with redistributed copies; the texts are in `THIRD_PARTY_LICENSES`.
