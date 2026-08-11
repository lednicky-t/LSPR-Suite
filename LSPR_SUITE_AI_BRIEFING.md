# LSPR Suite — AI Briefing Document

**Purpose:** This document is a comprehensive technical briefing for an AI assistant working on
the LSPR Suite codebase. It covers scientific context, repository structure, data models,
processing pipelines, GUI architecture, persistence, engineering rules, and current development
status. Read this before making any recommendations or code changes.

**Date generated:** 2026-06-29
**Codebase root:** `C:\Users\Admin\Documents\GitHub\LSPR-Suite`

---

## 1. Scientific Context

**LSPR (Localized Surface Plasmon Resonance)** is a nano-optics phenomenon used to detect
molecular binding events on nanoparticle or nanostructured sensor surfaces. As molecules bind
to the sensor, the resonance peak wavelength of the scattered or absorbed light shifts. This
shift is the primary measured signal.

The LSPR Suite software captures, stores, processes, and analyses LSPR data from two instrument
classes:

| Instrument type | App | Description |
|----------------|-----|-------------|
| **singleLSPR** | Acquisition + Evaluation | Fiber-optic cuvette spectrometer. Single spot per measurement. Measures full transmission spectrum at kHz rate, stores as HDF5. |
| **LSPRimaging** | Evaluation only (acquisition TBD) | Camera-based imaging sensor. Multiple ROIs (spots) per image. Stores image stacks (TIFF or OME-Zarr). Analyzes per-ROI absorbance spectra across wavelengths and frames. |

**Core measurement concept (LSPRimaging):**

- A hyperspectral image stack is acquired: one 2D image per wavelength × frame combination.
- A "reference" image (taken at no-binding baseline or a selected wavelength) is used to compute
  per-pixel absorbance: `A = log10(I_reference / I_sample)`.
- ROIs ("area ROIs" or "spots") define circular disk regions on the sensor. Each has a sample
  disk and a reference annular ring (used for local background).
- Absorbance spectra are computed per ROI per frame, fitted with a polynomial, and the peak
  wavelength or centroid is tracked over frames — giving the "sensorgram" (shift vs. time).

---

## 2. Repository Overview

### Top-Level Structure

```
LSPR-Suite/                            # Umbrella repository
├── apps/
│   ├── LSPRi/eva/                     # LSPRimaging Evaluation (git submodule)
│   ├── sLSPR/acq/                     # singleLSPR Acquisition (git submodule)
│   ├── sLSPR/eva/                     # singleLSPR Evaluation (git submodule)
│   └── suite_launcher/                # Suite launcher (in umbrella repo)
├── packages/
│   ├── lspr_core/                     # Shared domain models, experiment plan, units
│   ├── lspr_io/                       # Shared HDF5 schema/metadata helpers
│   └── lspr_ui/                       # Shared Qt theme tokens, icons, bootstrap
├── docs/
│   ├── architecture/                  # System design, pipeline docs
│   ├── decisions/                     # Architecture decision records
│   ├── schemas/                       # HDF5 format contracts
│   └── workflows/                     # Workflow documentation
├── tests/
│   ├── unit/                          # Pure logic tests (no Qt, no files)
│   └── integration/                   # Qt + HDF5 + workflow tests
├── analysis/                          # One-off analysis scripts (not core)
├── AGENTS.md                          # Full engineering policy for AI agents
├── CLAUDE.md                          # Repo topology quick-reference for AI
├── CHANGELOG.md                       # Version history
├── requirements.txt                   # pip editable installs for all packages
└── lspr_settings.json                 # Runtime UI state (gitignored, don't commit)
```

### App Entry Points

| Console script | App | Package | Run directly |
|----------------|-----|---------|--------------|
| `lspr-suite` | Suite Launcher | `suite_launcher` | `apps/suite_launcher/run.py` |
| `lspr-acquisition` | singleLSPR Acquisition | `lspr_app` | `apps/sLSPR/acq/run.py` |
| `lspr-single-evaluation` | singleLSPR Evaluation | `lspr_single_evaluation` | `apps/sLSPR/eva/run.py` |
| `lspri-evaluation` | LSPRimaging Evaluation | `lspr_imaging_app` | `apps/LSPRi/eva/run.py` |

### Setup

```powershell
git clone --recurse-submodules https://github.com/lednicky-t/LSPR-Suite.git
cd LSPR-Suite
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt   # installs all packages editable
```

Requirements (`requirements.txt`):
```
-e packages/lspr_ui
-e packages/lspr_core
-e packages/lspr_io
-e apps/suite_launcher
-e apps/sLSPR/acq
-e apps/sLSPR/eva
-e apps/LSPRi/eva
# Optional: python -m pip install AMFTools   (AMF M-Switch hardware)
```

Python ≥ 3.12 required. Windows target platform (also tested on Linux/Mac).

---

## 3. Shared Packages

### `packages/lspr_core` — Domain Models & Flow

**`models.py`** — Pydantic-based domain models:

```python
class SchemaInfo(BaseModel):
    name: str       # e.g. "lspr_experiment_plan"
    version: int    # ≥1

class SuiteIdentity(BaseModel):
    app_name: str
    app_version: str
    format_name: str
    format_version: int
    created_at_utc: str   # ISO timestamp

class ExperimentPlanStep(BaseModel):
    id: int
    label: str | None
    start_s: float
    end_s: float
    color: str | None       # hex color for visualization
    comment: str | None
    devices: dict[str, Any]

    @property
    def duration_s(self) -> float: ...
    def with_timing(start_s, end_s) -> ExperimentPlanStep: ...

class ExperimentPlan(BaseModel):
    schema_info: SchemaInfo
    identity: SuiteIdentity | None
    units: dict[str, str]
    steps: list[ExperimentPlanStep]

    def step_by_id(step_id: int) -> ExperimentPlanStep | None: ...
    def ordered() -> list[ExperimentPlanStep]: ...
```

**`flow.py`**:
```python
@dataclass(frozen=True)
class ExperimentPlanTimingSummary:
    step_count: int; total_duration_s: float; start_s: float; end_s: float

def summarize_experiment_plan(plan) -> ExperimentPlanTimingSummary
def retime_steps(steps, start_s) -> list[ExperimentPlanStep]
def shift_steps(steps, delta_s) -> list[ExperimentPlanStep]
```

**`launch_profiles.py`** — Three acquisition launch profiles:
- `LAUNCH_PROFILE_FULL` — real hardware, auto-connect
- `LAUNCH_PROFILE_SIMULATION` — simulated spectrometer
- `LAUNCH_PROFILE_CONTROL_EDITOR` — experiment plan editor only

```python
@dataclass(frozen=True, slots=True)
class LaunchProfileSpec:
    key: str; label: str; description: str
    force_simulator: bool; scan_devices: bool; start_live_acquisition: bool
    source_mode: str; show_left_controls: bool; show_sensorgram: bool
    # ... many UI visibility flags
```

---

### `packages/lspr_io` — HDF5 & File I/O

**Schema constants (`schema.py`)**:
```python
LSPR_MEASUREMENT_SCHEMA_NAME    = "lspr_measurement"
LSPR_MEASUREMENT_SCHEMA_VERSION = "5.2"   # major=5, minor=2
LSPR_SESSION_SCHEMA_VERSION     = 1
LSPR_EXPERIMENT_PLAN_SCHEMA_VERSION = 1
```

**HDF5 helpers (`hdf5.py`)** — Functions for reading/writing measurement metadata:
- `write_measurement_root_metadata(handle, ...)` — stamps schema, app identity, timestamps
- `write_measurement_manifest_metadata(group, ...)` — per-measurement group metadata
- `write_session_metadata(group, ...)` — session-level metadata
- `read_root_metadata(handle) -> dict`
- `validate_measurement_metadata(metadata) -> MeasurementFileValidation`

**HDF5 contract rules** (from `docs/schemas/hdf_standard.md`):
- Every file must carry `schema_name`, `schema_version`, `app_name`, `app_version`, `created_at_utc`.
- Raw data is **appended only, never overwritten**.
- Breaking changes → major version bump; additive changes → minor bump.
- Readers must reject unknown schema names and incompatible major versions.

---

### `packages/lspr_ui` — Qt Theme & Icons

**Theme (`theme.py`)** — `GuiTheme` dataclass with color tokens:

```python
@dataclass(frozen=True)
class GuiTheme:
    # Text
    text_primary: str = "#f8fafc"
    text_secondary: str = "#dbe5f3"
    text_muted: str = "#cbd5e1"
    text_dim: str = "#94a3b8"
    # Backgrounds
    window_bg: str = "#11161f"
    toolbar_bg: str = "#11161f"
    toolbar_section_bg: str = "#18212f"
    control_bg: str = "#243041"
    control_bg_hover: str = "#304155"
    control_border: str = "#324256"
    control_border_hover: str = "#60a5fa"
    # Status / accents
    accent_green: str = "#22c55e"
    accent_blue: str = "#38bdf8"
    accent_red: str = "#ef4444"
    accent_purple: str = "#a855f7"
    accent_gold: str = "#f59e0b"
    # Domain-specific
    spot_color: str = "#22c55e"         # ROI sample disk
    ring_color: str = "#94a3b8"         # ROI reference ring
    mask_color: str = "#ef4444"
    highlight_color: str = "#38bdf8"
    scale_bar_color: str = "#000000"
    # Icon sizing
    icon_button_outer: int = 28
    icon_button_inner: int = 24
    compact_icon_outer: int = 20
    compact_icon_inner: int = 16

BLUE_DARK_THEME = GuiTheme()        # default
GRAY_DARK_THEME = GuiTheme(...)     # alternate

def get_active_theme() -> GuiTheme
def set_active_theme(theme: GuiTheme) -> None
def apply_base_app_theme(app: QApplication, theme: GuiTheme) -> None
def hex_to_rgba(color: str, alpha: float) -> str
```

**Icons (`icons.py`)**:
- `tabler_icon(*names) -> QIcon` — from tabler-icons SVG library
- `tint_icon(icon, color, size) -> QIcon`
- `app_icon() -> QIcon`
- `standard_icon(kind, theme_mode) -> QIcon` — "minimize", "maximize", "close", "play", "record"

---

## 4. LSPRimaging Evaluation App — Full Detail

**Package:** `lspr_imaging_app`
**Location:** `apps/LSPRi/eva/src/lspr_imaging_app/`
**Entry point:** `app.py` → `main()`

### 4.1 Directory Map

```
lspr_imaging_app/
├── app.py                           # Application entry point
├── domain/
│   └── models.py                   # All dataclasses
├── processing/
│   ├── preprocess.py               # Spatial transforms, flatten, mask creation
│   ├── analysis.py                 # Absorbance, fitting, metrics
│   ├── spot_detection.py           # Auto-detect ROI positions
│   ├── chromatic.py                # Chromatic aberration correction
│   ├── chromatic_utils.py          # Chromatic helpers
│   ├── mask_utils.py               # Mask operation helpers
│   └── roi.py                      # ROI mask generation
├── gui/
│   ├── main_window.py              # Central QMainWindow (~12000 lines)
│   ├── main_window_icons.py        # Icon factory methods (mixin)
│   ├── layout_builder.py           # Widget/layout construction helpers
│   ├── analysis_controller.py      # Absorbance, sensorgram, selection
│   ├── analysis_tasks.py           # Thread-pool task functions
│   ├── background_profile_controller.py  # Background flattening preview
│   ├── chromatic_controller.py     # Chromatic correction workflow
│   ├── dataset_controller.py       # Dataset loading, folder management
│   ├── image_controller.py         # Image tool UI actions
│   ├── image_interaction_controller.py  # Mouse events on image view
│   ├── image_render_manager.py     # Image refresh pipeline
│   ├── mask_controller.py          # Mask drawing and settings
│   ├── overlay_manager.py          # Pyqtgraph overlay items (ROI, guide, etc.)
│   ├── panel_help_registry.py      # Help text for collapsible panels
│   ├── plot_manager.py             # Histogram and spectrum plots
│   ├── roi_overlay_helpers.py      # ROI circle/ring geometry helpers
│   ├── roi_table_controller.py     # ROI list widget management
│   ├── roi_table_helpers.py        # Row formatting helpers
│   ├── session_controller.py       # Dataset session logic
│   ├── session_state_manager.py    # Load/save processing profile
│   ├── shortcut_manager.py         # Keyboard shortcut dispatch
│   ├── shortcut_registry.py        # Shortcut definitions
│   ├── theme.py                    # App-specific theme overrides
│   ├── ui_helpers.py               # Small UI utility functions
│   ├── ui_state_manager.py         # Sync model ↔ UI controls ↔ QSettings
│   ├── widgets.py                  # Custom Qt widget classes
│   └── worker.py                   # Threading, undo/redo, overlay bundles
├── io/
│   └── dataset.py                  # Image stack loading, OME-Zarr, TIFF
└── storage/
    └── workspace.py                # JSON serialization/deserialization
```

---

### 4.2 Domain Models — `domain/models.py`

All use `@dataclass(slots=True)` unless noted. All support `dataclasses.asdict()` and
`dataclasses.replace()`.

```python
@dataclass(slots=True, frozen=True)
class ImageKey:
    wavelength_nm: float
    frame_index: int

@dataclass(slots=True)
class ImageRecord:
    key: ImageKey
    path: Path

@dataclass(slots=True)
class ImageDataset:
    folder: Path
    records: list[ImageRecord]
    source_format: str = "image_stack"  # or "ome_zarr"

    @property
    def wavelengths_nm(self) -> list[float]
    @property
    def frame_indices(self) -> list[int]
    @property
    def is_ome_zarr(self) -> bool
    @property
    def is_image_stack(self) -> bool
    @property
    def format_label(self) -> str

@dataclass(slots=True)
class CropDefinition:
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0
    enabled: bool = False

@dataclass(slots=True)
class PreprocessingSettings:
    image_tools_enabled: bool = True
    # Spatial transforms (applied in order: rotate → flip → crop)
    rotation_angle_deg: float = 0.0
    flip_horizontal: bool = False
    flip_vertical: bool = False
    crop: CropDefinition = field(default_factory=CropDefinition)
    # Units / calibration
    display_units: str = "px"           # "px" or "um"
    scale_bar_visible: bool = False
    calibration_enabled: bool = False
    microns_per_pixel_x: float = 1.0
    microns_per_pixel_y: float = 1.0
    measurement_anchor1_x_px: float = 0.0
    measurement_anchor1_y_px: float = 0.0
    measurement_anchor2_x_px: float = 100.0
    measurement_anchor2_y_px: float = 0.0
    # Background flattening
    flatten_background_enabled: bool = False
    flatten_background_sigma_px: float = 48.0
    flatten_background_binning: int = 2
    flatten_background_exclude_area_rois: bool = True
    flatten_background_exclude_mask: bool = False
    local_ring_normalization_enabled: bool = False
    # Chromatic aberration correction
    chromatic_correction_enabled: bool = False
    chromatic_registration_mode: str = "landmark_radial"
    chromatic_sample_image_count: int = 5
    chromatic_feature_count: int = 5
    chromatic_subpixel_precision: int = 4
    chromatic_tile_size_px: int = 96
    chromatic_search_radius_px: int = 24
    # Reference image selection
    reference_mode: str = "auto"        # "auto" or "manual"
    reference_wavelength_nm: float | None = None
    reference_frame_index: int = 0
    # Histogram highlight range
    histogram_highlight_min_value: float | None = None
    histogram_highlight_max_value: float | None = None

@dataclass(slots=True)
class AreaRoiDetectionSettings:
    # Detection
    mode: str = "dark"                  # "dark" or "bright"
    intensity_min_value: float | None = None
    intensity_max_value: float | None = None
    # Mask for detection
    mask_mode: str = "absolute"
    mask_profile_sigma_px: float = 48.0
    mask_relative_threshold_fraction: float = 0.18
    mask_local_contrast_sigma_px: float = 8.0
    mask_local_contrast_z_threshold: float = 3.0
    # ROI geometry
    sample_radius_px: float = 10.0
    reference_inner_radius_px: float = 14.0
    reference_outer_radius_px: float = 18.0
    # Pixel ignoring
    ignore_marked_pixels: bool = False
    ignored_intensity_value: float | None = None
    ignored_intensity_min_value: float | None = None
    ignored_intensity_max_value: float | None = None
    # Grid stamping
    array_rows: int = 0
    array_cols: int = 0
    array_spacing_px: int = 0

@dataclass(slots=True)
class MaskSettings:
    histogram_min_value: float | None = None
    histogram_max_value: float | None = None
    relative_threshold_fraction: float = 0.18
    relative_profile_sigma_px: float = 48.0
    local_contrast_sigma_px: float = 8.0
    local_contrast_z_threshold: float = 3.0
    morphology_radius_px: int = 2
    brush_size_px: int = 12
    histogram_enabled: bool = False
    histogram_mask: np.ndarray | None = None   # not serialized directly
    figure_enabled: bool = False
    figure_mask: np.ndarray | None = None      # not serialized directly

@dataclass(slots=True)
class AreaRoi:
    area_roi_id: int
    center_x: float
    center_y: float
    sample_radius_px: float
    sample_color_hex: str | None = None
    reference_color_hex: str | None = None
    sample_diameter_px: float | None = None
    reference_inner_diameter_px: float | None = None
    reference_outer_diameter_px: float | None = None
    score: float = 0.0
    support_mean_radius_px: float = 0.0
    support_radius_std_px: float = 0.0
    support_value_mean: float = 0.0
    support_value_std: float = 0.0
    quality_score: float = 0.0
    inferred: bool = False
    # NOTE: coordinates are in PROCESSED image space (after crop/rotation/flip)

@dataclass(slots=True)
class AreaRoiGroup:
    group_id: str
    name: str
    sample_color_hex: str = "#f59e0b"
    reference_color_hex: str = "#38bdf8"
    area_roi_ids: list[int] = field(default_factory=list)

@dataclass(slots=True)
class ChromaticTransformModel:
    frame_index: int
    wavelength_nm: float
    model_kind: str = "image_affine"
    affine_matrix: list[list[float]] = field(default_factory=lambda: [[1,0,0],[0,1,0]])
    global_shift_x_px: float = 0.0
    global_shift_y_px: float = 0.0
    rmse_px: float = 0.0
    mean_score: float = 0.0
    min_score: float = 0.0
    tile_count: int = 0
    inlier_count: int = 0

@dataclass(slots=True)
class ChromaticLandmarkObservation:
    landmark_id: int
    frame_index: int
    wavelength_nm: float
    x_px: float
    y_px: float

@dataclass(slots=True)
class RoiDefinition:
    roi_id: str
    name: str
    shape: str = "ellipse"
    center_x: float = 0.0
    center_y: float = 0.0
    size_x: float = 10.0
    size_y: float = 10.0
    background_padding_px: float = 10.0
    background_width_px: float = 12.0
    enabled: bool = True

@dataclass(slots=True)
class AbsorbanceSpectrumResult:
    wavelengths_nm: np.ndarray
    absorbance: np.ndarray
    sample_mean: np.ndarray
    reference_mean: np.ndarray
    sample_pixel_count: np.ndarray
    reference_pixel_count: np.ndarray
    load_seconds: float = 0.0
    roi_seconds: float = 0.0
    fit_seconds: float = 0.0
    total_seconds: float = 0.0
    area_roi_results: dict[int, AbsorbanceSpectrumResult] = field(default_factory=dict)

@dataclass(slots=True)
class FitResult:
    fitted_wavelengths_nm: np.ndarray
    fitted_absorbance: np.ndarray
    coefficients: np.ndarray
    peak_wavelength_nm: float | None
    centroid_nm: float | None
    peak_absorbance: float | None

@dataclass(slots=True)
class AnalysisState:
    dataset: ImageDataset | None = None
    rois: list[RoiDefinition] = field(default_factory=list)
    preprocessing: PreprocessingSettings = field(default_factory=PreprocessingSettings)
    area_roi_settings: AreaRoiDetectionSettings = field(default_factory=AreaRoiDetectionSettings)
    area_rois: list[AreaRoi] = field(default_factory=list)
    area_roi_groups: list[AreaRoiGroup] = field(default_factory=list)
    chromatic_models: list[ChromaticTransformModel] = field(default_factory=list)
    chromatic_landmarks: list[ChromaticLandmarkObservation] = field(default_factory=list)
    mask: MaskSettings = field(default_factory=MaskSettings)
```

**Backward compatibility aliases** (old names from before rename): removed as of the 2026-08
sample-ROI/reference-ROI terminology pass. `DetectedSpot`, `SpotGroup`, and `SpotDetectionSettings`
no longer exist — use `AreaRoi`, `AreaRoiGroup`, `AreaRoiDetectionSettings` directly. Persisted
JSON files from before the rename are still readable via the legacy-key fallbacks in
`storage/workspace.py` (e.g. `spot_detection`, `detected_spots`, `spot_groups`, `spot_radius_px`,
`ring_inner_radius_px`, etc.), just not via a Python alias.

---

### 4.3 Processing Pipeline

#### `processing/preprocess.py` — Spatial transforms + image corrections

Transform order (applied strictly in sequence):
1. **Rotation** — `scipy.ndimage.rotate(reshape=True)` at `rotation_angle_deg`
2. **Flip horizontal** — `numpy.fliplr`
3. **Flip vertical** — `numpy.flipud`
4. **Crop** — array slicing at `[y0:y1, x0:x1]` — **only applied if `image_tools_enabled=True`**
5. **Background flattening** — optional Gaussian-based background subtraction
6. **Local ring normalization** — optional per-pixel normalization by local ring mean

Key functions:
```python
def apply_preprocessing(image, settings, rois=None, mask_settings=None,
                         external_mask=None, ...) -> np.ndarray

def _apply_spatial_transform(image, settings, *, order, mode, cval) -> np.ndarray
    # Implements steps 1-4 above.
    # IMPORTANT: if not image_tools_enabled → returns after steps 1-3 (skip crop)

def spatial_output_shape(image_shape, settings) -> tuple[int, int]
    # Probes the transform on a zero array to get exact output shape.
    # Necessary because ndimage.rotate with reshape=True can differ by 1px from trig.

def spatial_coordinate_maps(image_shape, settings) -> tuple[np.ndarray, np.ndarray]
    # Returns (x_map, y_map) arrays mapping processed coords → original coords

def flatten_background(image, *, sigma_px, binning, rois, external_mask) -> np.ndarray
    # Subtracts a Gaussian-blurred background estimate.
    # ROI areas and masked pixels are excluded from background estimation.

def estimate_background_profile(...) -> np.ndarray
    # Computes the background profile for preview display.

def create_histogram_mask(image, settings) -> np.ndarray
    # Creates bool mask where intensity is outside [min, max].

def create_figure_mask(image, settings, mode) -> np.ndarray
    # Creates spatial mask by thresholding (absolute, relative, local contrast).

def apply_morphology_to_mask(mask, operation, radius_px) -> np.ndarray
    # Erosion or dilation using disc structuring element.
```

**Critical note on `image_tools_enabled`:** This flag controls whether the crop is applied
in the pipeline. When the user activates the crop or rotation tool in the GUI, this flag is
temporarily set to `False` (preview mode) so the full image is visible for adjustment. If the
session is saved and restored while in preview mode, the crop will not be applied on restore
(fixed in recent session, see §8).

#### `processing/analysis.py` — Absorbance computation & spectral fitting

```python
# Core formula
def absorbance_from_means(roi_mean, background_mean) -> float:
    return log10(background_mean / roi_mean)

def fit_absorbance_curve(
    wavelengths_nm, absorbance,
    poly_order: int = 3,       # polynomial degree (default 3rd order)
    wl_min=None, wl_max=None,  # wavelength window for fitting
    sample_count: int = 400,   # interpolation points for smooth curve
) -> FitResult
    # Polynomial fit. Returns peak (argmax) and centroid (weighted mean).

def fit_roi_spectrum(spectrum: RoiSpectrum, poly_order=3, ...) -> FitResult

def metric_value_from_fit(fit, metric_key) -> tuple[float | None, float | None]
    # metric_key: "maximum" (peak wavelength) or "centroid"

def extract_roi_spectrum(dataset, roi, frame_index) -> RoiSpectrum
def extract_metric_series(dataset, roi, poly_order, wl_min, wl_max) -> RoiMetricSeries
def export_roi_series_csv(dataset, rois, destination) -> None
```

#### `processing/spot_detection.py` — Auto-detect ROI positions

```python
def detect_spots(
    image: np.ndarray,
    settings: AreaRoiDetectionSettings,
    external_mask=None,
    progress_callback=None,
) -> list[AreaRoi]
    # 1. Creates a detection mask (absolute threshold or contrast-based).
    # 2. Finds local minima (dark mode) or maxima (bright mode).
    # 3. Scores each candidate by sample/ring contrast ratio.
    # 4. Filters by array geometry (rows × cols × spacing) if configured.
    # 5. Returns list of AreaRoi, sorted by quality_score descending.

def refresh_roi_metrics(image, settings, rois, external_mask) -> list[AreaRoi]
    # Re-computes quality scores for existing ROIs without repositioning them.

def ignored_pixel_mask(image, settings, external_mask) -> np.ndarray
    # Bool mask of pixels excluded from detection/analysis.
```

#### `processing/chromatic.py` — Chromatic aberration correction

Chromatic aberration causes different wavelengths to be spatially offset. Correction aligns
all wavelength images to a reference wavelength via affine transforms.

```python
def detect_regional_landmarks(image, feature_count, *, patch_radius_px, subpixel_precision)
    -> dict[int, tuple[float, float]]
    # Divides image into grid regions, detects best corner feature per region.
    # Returns {landmark_id: (x_px, y_px)}.

def track_landmarks(reference_image, target_image, reference_landmarks, *,
                    search_radius_px, patch_radius_px, subpixel_precision)
    -> dict[int, tuple[float, float]]
    # Cross-correlates patches to track reference landmarks in target image.

def estimate_affine_chromatic_transform(...) -> ChromaticRegistrationResult
    # Fits affine matrix from tracked landmark correspondences.
    # Uses RANSAC-style inlier filtering.

def warp_image_affine(image, affine_matrix, output_shape=None) -> np.ndarray
    # Applies affine warp using scipy.ndimage.affine_transform.

def transformed_disk_mask(image_shape, center_x, center_y, radius, affine_matrix) -> np.ndarray
def transformed_annulus_mask(image_shape, center_x, center_y, inner_r, outer_r, affine_matrix) -> np.ndarray
    # Generate pixel masks with chromatic correction applied.
    # Used for per-wavelength ROI pixel extraction.

def transform_spots_affine(spots, affine_matrix) -> list[AreaRoi]
    # Transform ROI center coordinates through affine matrix.
    # Used to show corrected ROI positions in non-reference wavelengths.
```

---

### 4.4 Storage / Persistence — `storage/workspace.py`

All state is persisted as JSON alongside the dataset folder. Two files per dataset:
- `<dataset_name>_preprocessing.json` — preprocessing settings only (legacy format)
- `<dataset_name>_profile.json` — full processing profile (current format)

```python
PROCESSING_PROFILE_VERSION = 3     # bumped on breaking schema changes

def save_processing_profile(
    path: Path,
    preprocessing: PreprocessingSettings,
    area_roi_settings: AreaRoiDetectionSettings,
    area_rois: list[AreaRoi],
    area_roi_groups: list[AreaRoiGroup] | None,
    rois: list[RoiDefinition] | None,
    chromatic_models: list[ChromaticTransformModel] | None,
    chromatic_landmarks: list[ChromaticLandmarkObservation] | None,
    analysis_cache: dict | None,
    session_mask: dict | None,
    mask_settings: MaskSettings | None,
) -> None

def load_processing_profile(path: Path) -> tuple[
    PreprocessingSettings,
    AreaRoiDetectionSettings,
    list[AreaRoi],
    list[AreaRoiGroup],
    list[RoiDefinition],
    list[ChromaticTransformModel],
    list[ChromaticLandmarkObservation],
    dict,           # analysis_cache
    dict | None,    # session_mask
    MaskSettings,
]
```

**Backward compatibility** built into loader:
- `"spot_detection"` key → `area_roi_settings`
- `"detected_spots"` key → `area_rois`
- `"spot_groups"` key → `area_roi_groups`
- `"spot_radius_px"` → `sample_radius_px`
- `"flatten_background_exclude_spots"` → `flatten_background_exclude_area_rois`

**Mask serialization** — masks are large numpy arrays, stored as base64-encoded packed bits:
```python
def _encode_mask_payload(mask: np.ndarray) -> dict  # shape + base64 packed bits
def _decode_mask_payload(payload: dict) -> np.ndarray | None
```

**QSettings** persistence (Qt-managed, stored in Windows registry / ~/.config):
- Window geometry and layout (`layout/*`)
- Visual preferences (`visual/*` — colors, alphas, visibility flags)
- Analysis settings (`analysis/*`)
- Histogram settings
- UI theme and scale factor (`ui/*`)

---

### 4.5 I/O — `io/dataset.py`

```python
def load_dataset(folder: Path, format_hint=None) -> ImageDataset
    # Auto-detects TIFF stack vs. OME-Zarr.
    # TIFF: scans filenames for wavelength/frame patterns.
    # OME-Zarr: reads metadata from .zattrs.

def load_image_array(path_str: str) -> np.ndarray
    # Supports TIFF (tifffile), OME-Zarr (zarr), possibly others.
    # Returns 2D float32 array normalized to [0, 65535].

def dataset_record_map(dataset) -> dict[tuple[int, float], ImageRecord]
    # Fast lookup: (frame_index, wavelength_nm) → ImageRecord

def export_ome_zarr_dataset(dataset, destination, chunk_size_px, compression_enabled,
                             *, cancel_event, progress_callback) -> Path
    # Converts TIFF stack to OME-Zarr for better chunking/streaming performance.
```

---

### 4.6 Application Entry Point — `app.py`

```python
def _apply_ui_scale_factor() -> None
    # Reads "ui/scale_factor" from QSettings("LSPR", "LSPRImaging").
    # If not "auto", sets os.environ["QT_SCALE_FACTOR"] = str(factor).
    # MUST be called BEFORE QApplication is created.
    # Valid range: 0.5 to 3.0.

def main() -> None:
    # 1. _apply_ui_scale_factor()         # must be first
    # 2. _configure_logging()            # file logging to logs/lspr_imaging_YYYYMMDD_HHmmss.log
    # 3. QApplication(sys.argv)
    # 4. Apply dark theme + Windows titlebar
    # 5. Show splash screen with progress
    # 6. Create MainWindow(default_folder, fast_startup)
    # 7. Schedule startup restore flow (QTimer.singleShot)
    # 8. app.exec()
```

**Splash progress milestones:** 8% → 40% → 55% → 65% → 96% → 100%

**Restart pattern** (for UI scale change):
```python
subprocess.Popen([sys.executable] + sys.argv)
self.close()
# NOTE: must use [sys.executable] + sys.argv, NOT bare sys.argv
# Reason: .py files are not Win32 executables; bare sys.argv causes WinError 193
```

---

### 4.7 GUI Architecture — `gui/main_window.py`

`MainWindow` inherits from `MainWindowIcons, QMainWindow`. It is the central class (~12000 lines)
and delegates to specialised controllers via composition.

#### Key State Attributes

```python
# Core state
self._state: AnalysisState

# Image display
self._current_image_key: tuple[int, float] | None   # (frame_index, wavelength_nm)
self._current_processed_image: np.ndarray | None
self._current_record_path: Path | None
self._frame_values: list[int]
self._wavelength_values: list[float]

# Caches (LRU, fixed-size OrderedDicts)
self._processed_image_cache              # max 6
self._absorbance_spectrum_cache          # max 48
self._sensorgram_cache                   # max 48
self._spot_absorbance_cache              # max 512

# Overlay items (pyqtgraph)
self._roi_overlay_items: dict[int, RoiOverlayBundle]
self._guide_overlay_items: dict[int, GuideOverlayBundle]
self._landmark_overlay_items: dict[int, LandmarkOverlayBundle]

# Selection / editing
self._selected_roi_ids: set[int]
self._active_tool: str | None           # "crop", "rotate", "roi", "mask", "measure", etc.
self._roi_editor_mode: str              # "circles" or "rectangles"

# Image tool preview state
self._image_tools_preview_only: bool    # True = crop tool active, image shown uncropped
self._image_tools_pre_preview_enabled: bool  # saved image_tools_enabled before preview

# Threading
self._thread_pool: QThreadPool
self._image_refresh_running: bool
self._absorbance_spectrum_running: bool
self._sensorgram_running: bool

# Undo/redo
self._undo_stack: list[UndoSnapshot]    # max 5 entries
self._redo_stack: list[UndoSnapshot]

# Controllers
self._dataset_controller: DatasetController
self._image_controller: ImageController
self._roi_table_controller: RoiTableController
self._mask_controller: MaskController
self._chromatic_controller: ChromaticController
self._analysis_controller: AnalysisController
self._plot_manager: PlotManager
self._ui_state_manager: UIStateManager
self._session_state_manager: SessionStateManager
self._shortcut_manager: ShortcutManager
self._image_interaction: ImageInteractionController
self._bg_profile: BackgroundProfileController
self._overlay_manager: OverlayManager
```

#### Important Constants

```python
SETTINGS_ORG = "LSPR"
SETTINGS_APP = "LSPRImaging"
HISTOGRAM_MIN_INTENSITY = 0.0
HISTOGRAM_MAX_INTENSITY = 65535.0
PROCESSED_IMAGE_CACHE_SIZE = 6
ABSORBANCE_SPECTRUM_CACHE_SIZE = 48
SPOT_ABSORBANCE_CACHE_SIZE = 512
SENSORGRAM_CACHE_SIZE = 48
UNDO_STACK_LIMIT = 5
```

#### Controller Responsibilities

| Controller | File | Responsibilities |
|-----------|------|----------------|
| `DatasetController` | `dataset_controller.py` | Load folder, discover files, switch datasets |
| `ImageController` | `image_controller.py` | Frame/wavelength navigation, image tool actions |
| `ImageRenderManager` | `image_render_manager.py` | Async image load+process pipeline |
| `ImageInteractionController` | `image_interaction_controller.py` | Mouse events (pan, crop drag, ROI click, etc.) |
| `AnalysisController` | `analysis_controller.py` | Absorbance spectrum, sensorgram, ROI selection highlight |
| `RoiTableController` | `roi_table_controller.py` | ROI list widget, row selection, style management |
| `MaskController` | `mask_controller.py` | Mask drawing, mode switching |
| `ChromaticController` | `chromatic_controller.py` | Landmark placement, auto-detect, affine fitting |
| `BackgroundProfileController` | `background_profile_controller.py` | Background preview display |
| `OverlayManager` | `overlay_manager.py` | All pyqtgraph overlay items (ROI circles, guides, landmarks) |
| `PlotManager` | `plot_manager.py` | Histogram, spectrum, sensorgram plots |
| `UIStateManager` | `ui_state_manager.py` | Sync `AnalysisState` ↔ Qt control widgets ↔ QSettings |
| `SessionStateManager` | `session_state_manager.py` | Load/save processing profile, startup restore |
| `ShortcutManager` | `shortcut_manager.py` | Keyboard shortcut dispatch |

#### Image Render Pipeline

```
_refresh_image()
  ↓
_capture_pending_image_view_ranges()      # save viewport before change
  ↓
ImageRenderManager.refresh_image()
  ↓  [QThreadPool worker]
_process_image_task(path, preprocessing, rois, mask)  # load + apply_preprocessing()
  ↓  [back on main thread via signal]
_apply_loaded_image(processed, path, key, ...)
  ↓
_set_image_item(processed)               # update pyqtgraph ImageItem
_sync_rotation_tool()                    # update rotation UI
_sync_crop_tool(image_shape)             # update crop ROI overlay position/size
_update_roi_overlays()                   # redraw ROI circles
_update_histogram(processed)             # update histogram plot
_restore_pending_image_view_ranges()     # restore viewport
```

#### Session Restore Sequence

```
SessionStateManager.run_startup_restore_flow()
  1. _restore_window_geometry()
  2. _restore_layout_preferences()
  3. DatasetController.run_startup_restore_flow()   # find and load last dataset
  4. load_processing_profile(path)                   # load JSON → AnalysisState
  5. _sync_roi_detection_controls()
  6. _restore_control_preferences()
     → sync_image_processing_controls()             # model → UI controls (signals blocked)
     → sync_roi_detection_controls()
  7. _analysis_enabled = False                      # always start with analysis off
  8. _refresh_image()                               # first image render
  9. window.showNormal()
```

#### UI Scale Factor / DPI Support

- `QT_SCALE_FACTOR` env var must be set **before** `QApplication` is created.
- `app.py::_apply_ui_scale_factor()` reads from `QSettings` and sets the env var.
- User changes scale via **View → UI Scale** submenu (9 options: Auto, 75%–200%).
- Changing scale prompts for restart; restart uses `subprocess.Popen([sys.executable] + sys.argv)`.
- On restart, the new env var is set before QApplication.

#### Spinbox Width Calculation

All spinboxes in the Workflow panel use `_set_spinbox_width(spinbox, sample_text)`:

```python
def _set_spinbox_width(self, spinbox, text, *, minimum=46) -> None:
    suffix = spinbox.suffix()
    full_text = text if (not suffix or text.endswith(suffix)) else text + suffix
    text_w = spinbox.fontMetrics().horizontalAdvance(full_text)
    # Use sizeHint() to derive overhead (buttons + frame).
    # sizeHint() uses Qt internal style metrics — correct at any DPI, before widget is shown.
    sh_w = spinbox.sizeHint().width()
    max_text = spinbox.prefix() + spinbox.textFromValue(spinbox.maximum()) + spinbox.suffix()
    overhead = max(sh_w - spinbox.fontMetrics().horizontalAdvance(max_text), 30)
    spinbox.setFixedWidth(max(minimum, text_w + overhead))
```

This is called at init (`_apply_compact_control_widths`) and again after unit suffix changes
(`_update_display_unit_controls`). The sizeHint approach was chosen because `subControlRect`
returns zero-width for widgets not yet placed in a layout.

#### Image Tool Preview Mode (crop/rotation)

The crop and rotate tools use a "preview mode" pattern:
- When activated: `_unlink_image_tools_for_preview()` → sets `image_tools_enabled=False`
  so the full (uncropped) image is shown for editing.
- Pipeline: `_apply_spatial_transform` skips the crop step when `image_tools_enabled=False`.
- When user clicks Apply: `_on_image_tools_section_applied_changed(True)` → sets
  `image_tools_enabled=True`, saves, re-renders with crop applied.
- **Bug fixed (2026-06-29):** Preview mode state was being persisted to disk. On restore,
  `image_tools_enabled=False` meant the crop was not applied. Fix: `save_processing_state_for_dataset`
  now substitutes `_image_tools_pre_preview_enabled` (the pre-preview value) when saving during
  preview mode, via `dataclasses.replace()`.

---

### 4.8 GUI Widgets — `gui/widgets.py`

Custom PyQt6 widget classes:

```python
class ResponsiveDoubleSpinBox(QDoubleSpinBox):
    # Overrides stepBy() for smoother value increment/decrement.

class CollapsibleSection(QWidget):
    # Accordion-style panel with expand/collapse, optional apply toggle.
    # Signals: expanded_changed, apply_changed
    # Methods: set_applied(bool), is_applied() -> bool,
    #          set_expanded(bool), is_expanded() -> bool,
    #          has_apply_toggle() -> bool
    #          set_pinned(bool)

class PanelContainer(QWidget):
    # Panel with title bar and visibility toggle.

class CompactWedgeSlider(QWidget):
    # Rotary-style slider for angle/float input.

class BusySpinner(QWidget):
    # Animated spinner (shown during long operations).

class ShineProgressBar(QProgressBar):
    # Custom styled progress bar.

class ClickableIconLabel(QLabel):
    # Emits clicked() on mouse press.

class FreeStandingToggleIconLabel(ClickableIconLabel):
    # Icon-based toggle (like a checkbox).

class FreeStandingToggleTextLabel(ClickableIconLabel):
    # Text-based toggle.
```

---

### 4.9 Threading — `gui/worker.py`

```python
class WorkerSignals(QObject):
    result = pyqtSignal(object)
    error = pyqtSignal(str)
    finished = pyqtSignal()

class FunctionWorker(QRunnable):
    # Wraps any callable for QThreadPool execution.
    # Emits result/error/finished signals on completion.

@dataclass(slots=True)
class RoiOverlayBundle:
    curve: pg.PlotCurveItem
    ring_fill: pg.FillBetweenItem | None
    inner_curve: pg.PlotCurveItem | None
    outer_curve: pg.PlotCurveItem | None
    label: pg.TextItem | None

@dataclass(slots=True)
class GuideOverlayBundle: ...
@dataclass(slots=True)
class LandmarkOverlayBundle: ...
@dataclass(slots=True)
class MeasurementOverlayBundle: ...
@dataclass(slots=True)
class ScaleBarOverlayBundle: ...

class UndoSnapshot:
    # Full copy of AnalysisState for undo/redo (max 5 entries each direction).

class WorkflowLogBridge(QObject):
    message_logged = pyqtSignal(str, int)

class WorkflowLogHandler(logging.Handler):
    # Routes Python logging to the GUI workflow log panel.
```

---

### 4.10 Panel Layout Overview

The main window has a three-column layout:
- **Left panel** — collapsible workflow sections (Dataset, Image Tools, ROI Editor, Background,
  Chromatic, Mask, Analysis)
- **Center** — image view (`pg.ImageView` / `pg.PlotWidget`) with toolbar across the top
- **Right panel** — ROI list + Spectrum / Sensorgram tabs

The workflow (left) panel contains these collapsible sections:

| Section | Controls |
|---------|---------|
| Dataset | Folder path, dataset format info, frame/wavelength navigation sliders |
| Image Tools | Rotation angle, crop, flip controls; calibration ruler; scale bar |
| ROI Editor | Sample diameter, reference inner/outer diameter, array grid, detection mode |
| Background | Sigma, binning, exclude ROIs/mask toggles |
| Chromatic | Enable toggle, sample count, feature count, landmark controls |
| Mask | Histogram range, figure mode, brush size, morphology |
| Analysis | Polynomial order, metric selector (peak / centroid), frame range |

---

### 4.11 QSettings Keys Reference

```
# Layout
layout/roi_list_visible
layout/cached_rois_only_visible
layout/panel_layout
layout/window_geometry
layout/window_state
image_tools_panel_pinned
image_tools_panel_expanded

# Visual (colors are hex strings, alphas are floats 0.0-1.0)
visual/mask_color
visual/spot_color
visual/ring_color
visual/highlight_color
visual/scale_bar_color
visual/mask_alpha
visual/spot_alpha
visual/ring_alpha
visual/highlight_alpha
visual/spots_visible
visual/spot_labels_visible
visual/mask_visible
visual/rings_visible
visual/highlight_visible
visual/reference_points_visible
visual/chromatic_reference_points_all_visible
visual/background_profile_visible
visual/image_view_x_min, _x_max, _y_min, _y_max

# Histogram
histogram_bin_size
histogram/log_y
histogram/highlight_min
histogram/highlight_max

# Analysis
analysis/poly_order
analysis/metric            # "maximum" or "centroid"
analysis/frame_start
analysis/frame_end
analysis/live_preview

# Controls
controls/live_geometry
selection/area_roi_ids

# Mask tools
mask_tools/relative_sigma_px
mask_tools/relative_threshold_percent
mask_tools/local_sigma_px
mask_tools/local_z

# App-wide
ui/theme                    # "gray" or "blue"
ui/scale_factor             # "auto" or float string e.g. "1.25"
startup/fast_startup
analysis_section_applied
analysis/live_preview
```

---

## 5. singleLSPR Acquisition App — Overview

**Package:** `lspr_app`
**Location:** `apps/sLSPR/acq/src/lspr_app/`

Primary purpose: real-time LSPR spectrum acquisition, live display, and HDF5 recording.

### Directory map

```
lspr_app/
├── app.py                    # Entry point
├── gui/
│   ├── main_window*.py       # Main window (split across multiple files by lifecycle/layout)
│   ├── experiment_control_*.py  # Experiment plan/flow control subsystem
│   └── workers.py            # Background acquisition workers
├── device/
│   ├── base.py               # Device interface ABC
│   ├── simulated.py          # Simulated spectrometer (for tests/simulation)
│   ├── ocean.py              # Ocean Insight seabreeze backend
│   └── (other device backends)
├── domain/                   # Typed data models (measurement, pump plan, session)
├── storage/                  # HDF5 recording (async file writing)
└── diagnostics.py            # Runtime diagnostics and probe
```

### Architecture rules (from `docs/` in this app)

Core rule: **acquisition and file writing must be lossless; UI processing may skip stale frames.**

- Raw recording pipeline: Spectrometer → Worker thread → HDF5 write (never dropped)
- UI pipeline: latest frame → display (may skip intermediate frames)
- Workers emit signals; GUI slots consume them; no shared mutable state between threads

---

## 6. singleLSPR Evaluation App — Overview

**Package:** `lspr_single_evaluation`
**Location:** `apps/sLSPR/eva/src/lspr_single_evaluation/`

Loads HDF5 files produced by the acquisition app, displays spectra and sensorgrams, allows
export of results.

---

## 7. Suite Launcher — `apps/suite_launcher`

Shows a card for each app, allows launching with profile selection (for singleLSPR Acquisition).
Remembers last-opened app and auto-launches after ~3 seconds.

```python
# targets.py — app path resolution
def resolve_app_path(app_name: str) -> Path
    # Tries repo-relative paths, then env var overrides:
    # LSPR_LEGACY_SINGLE_ROOT
    # LSPR_LEGACY_EVAL_ROOT
    # LSPR_LEGACY_IMAGING_ROOT
```

---

## 8. Recent Bug Fixes & Current Development Status

This section documents bugs discovered and fixed during recent active development sessions.
These are important context for understanding invariants and edge cases.

### 8.1 Rename: spot → ROI (partially complete, ongoing)

A large rename from `spot`/`DetectedSpot`/`SpotGroup`/`spot_detection` naming to
`roi`/`AreaRoi`/`AreaRoiGroup`/`area_roi_settings` was performed. Several rename artifacts
remain as subtle variable-scope bugs where:
- A loop iterates `for roi in ...` but the body uses `spot.attr` (wrong variable)
- A generator `(spot for roi in ...)` yields the wrong variable
- A dict comprehension `{roi.x: spot for roi in ...}` keys/values mismatch
- Assignment `spot = AreaRoi(...)` then `append(roi)` (variable not renamed consistently)

**Fixed locations** (from recent sessions):
- `gui/main_window.py::_update_roi_summary` — `display_spot` → `display_roi`
- `gui/plot_manager.py::spot_spectrum_color` — generator used wrong var
- `gui/main_window.py::_add_roi_array_at` — `spot = AreaRoi(...)` → `roi = AreaRoi(...)`
- `processing/spot_detection.py::support_score` — inner function body used `roi.*` instead of parameter `spot.*`
- `gui/chromatic_controller.py:561` — `_normalized_odd_count(...)` → `self.window._normalized_odd_count(...)`
- `gui/ui_state_manager.py:207` — `window._selected_area_roi_ids` → `window._selected_roi_ids`
- `gui/analysis_tasks.py:260-270` — dict comprehension keyed by `int(spot.area_roi_id)` but iterated `for roi in selected_rois`

**Systematic scan performed**: grep patterns `(spot for roi in ...)`, `{roi.x: spot for roi in ...}`,
`spot = AreaRoi(...)` followed by `append(roi)`. All found instances fixed. However, less-exercised
code paths may still contain artifacts.

### 8.2 `image_tools_enabled` not persisted correctly during preview mode

**Problem:** When the user activates the crop or rotate tool, `_unlink_image_tools_for_preview()`
immediately sets `preprocessing.image_tools_enabled = False` AND saves the profile. If the app
is closed or the tool is deactivated without clicking "Apply", the profile permanently has
`image_tools_enabled = False`. On the next session, the crop is not applied to the pipeline
(see `preprocess.py:128`), so the image appears uncropped.

**Fix:** `MainWindow` now tracks `_image_tools_pre_preview_enabled` (the value before entering
preview mode). `save_processing_state_for_dataset` in `session_state_manager.py` substitutes this
pre-preview value when `_image_tools_preview_only` is True, using `dataclasses.replace()` on
the `PreprocessingSettings` object before passing it to the save functions.

### 8.3 Spinbox width calculated before widget is visible

**Problem:** `_set_spinbox_width` previously used `QStyle.subControlRect(SC_SpinBoxUp)` to
measure button width. This returns zero when the widget has not yet been placed in a layout
(geometry is 0×0). This made the overhead ~6px instead of the correct ~65px, causing all
spinboxes to be set far too narrow. Text was visually clipped by the spinner buttons.

**Fix:** Now uses `spinbox.sizeHint().width()` to derive overhead. Qt's `sizeHint()` uses
internal style PM metrics and is always correct regardless of whether the widget has been shown.
`overhead = max(sizeHint.width() - fontMetrics.horizontalAdvance(maxValueText + suffix), 30)`.

### 8.4 `QStyleOptionSpinBox` not in PyQt6.QtWidgets

**Problem:** After adding `QStyleOptionSpinBox` to the import, the app crashed with
`NameError: name 'QStyleOptionSpinBox' is not defined` because the import was inserted at
the wrong position in the import block (encoding issue with µ character in the surrounding code
caused the string replace to fail silently). Later made redundant by the sizeHint fix (§8.3)
which removed the need for `QStyleOptionSpinBox` entirely.

### 8.5 Windows restart WinError 193

**Problem:** Restart for UI scale change used `subprocess.Popen(sys.argv)` which fails with
`OSError: [WinError 193] %1 is not a valid Win32 application` because `.py` files are not
Win32 executables.

**Fix:** Changed to `subprocess.Popen([sys.executable] + sys.argv)`.

### 8.6 Suffix clipping in spinboxes

**Problem:** `_set_spinbox_width` appended the suffix to the sample text (`"9999"` → `"9999 µm"`)
but the suffix was not set yet when `_apply_compact_control_widths` ran. Later, when
`_update_display_unit_controls` set the suffix, it re-called `_set_spinbox_width` — but that
call only had access to whatever suffix was set at call time.

**Fix:** `_set_spinbox_width` auto-appends `spinbox.suffix()` to the sample text if not already
present. `_update_display_unit_controls` also calls `_set_spinbox_width` after setting the
suffix, ensuring final widths account for the actual suffix.

---

## 9. Engineering Rules & Conventions

### Priority Order (from AGENTS.md)

1. Correctness and scientific validity
2. Data integrity and reproducibility
3. Maintainability and readability
4. Modularity and testability
5. Performance and memory efficiency
6. GUI polish and user convenience

### Critical Rules

**Scientific code must be separate from GUI code.** Analysis functions in `processing/` must
work without a Qt application running.

**Raw data is sacred.** Never overwrite raw measurement data in HDF5. Derived results go in
separate groups/files.

**Acquisition pipeline must be lossless.** GUI display may skip frames but file recording
must capture every sample.

**No `showPopup()` during widget construction.** Default popup readiness to `False`; enable
only after startup wiring completes. Use explicit state propagation, not `getattr(..., True)`.

**GUI thread must not block.** Image loading, processing, fitting, and analysis run in the
thread pool (`QThreadPool`). Workers emit Qt signals to drive UI updates.

**Backward compatibility in file loading.** All load functions handle both old and new key
names with graceful fallbacks.

**Coordinate space awareness.** All `AreaRoi` coordinates are in PROCESSED image space (after
rotation, flip, and crop). If `image_tools_enabled=False`, "processed" means after rotation/flip
but NOT crop. Chromatic corrections are per-wavelength affine transforms applied on top of
preprocessing.

### Submodule Workflow

App code lives in individual git repos (submodules). To change app code:
1. Edit inside the submodule directory
2. Commit/push from within that directory (separate git repo)
3. Update the pointer in the umbrella repo: `git add apps/LSPRi/eva && git commit -m "bump"`

### Signal/Slot Pattern

- Use `blockSignals(True/False)` around all programmatic widget updates to prevent cascades.
- Controllers use the `blockSignals` / restore pattern consistently.
- `_set_section_applied` always blocks signals before calling `section.set_applied()`.

### Cache Invalidation

- Signature-based: compute a tuple of relevant inputs (image shape, ROI ids, preprocessing
  fingerprint, etc.) and use it as the cache key.
- Explicit invalidation: `_invalidate_image_analysis_caches()` called on any preprocessing change.
- LRU eviction via `OrderedDict.popitem(last=False)` when size exceeds limits.

### Undo/Redo

- `_push_undo_point(label)` saves a deep copy of `AnalysisState` to `_undo_stack` (max 5).
- Undo pops from undo stack, pushes current to redo stack.
- Applied before any user-visible state change (ROI move, geometry change, etc.).

---

## 10. Test Infrastructure

```
tests/
├── unit/      # Pure Python, no Qt, no files, fast
└── integration/  # Qt app, HDF5 files, device mocks, workflow scenarios
```

```powershell
python -m pytest tests/           # all tests
python -m pytest tests/unit/      # fast subset
python -m pytest tests/integration/
```

Rules:
- Use tolerances for floating-point assertions, not exact equality.
- No real hardware required. Simulated devices replace all hardware.
- Small, deterministic fixtures in `tests/data/`.
- Test edge cases: empty data, saturated images, missing metadata, zero ROIs.

---

## 11. Technology Stack

| Library | Version / notes | Usage |
|---------|-----------------|-------|
| Python | ≥ 3.12 | Language |
| PyQt6 | latest | GUI framework |
| pyqtgraph | latest | Image view, overlays, plots |
| NumPy | latest | All array operations |
| SciPy | latest | Rotation, fitting, interpolation, morphology |
| tifffile | latest | TIFF stack loading |
| zarr | latest | OME-Zarr reading/writing |
| h5py | latest | HDF5 file access |
| pydantic | v2 | Shared domain models (lspr_core) |
| AMFTools | optional | AMF M-Switch hardware support |

---

## 12. Known Open Items & Future Work

- **LSPRimaging Acquisition app** — reserved, not yet started.
- **Remaining spot→ROI rename artifacts** — less-exercised code paths may still contain
  `spot` variable mismatches. A comprehensive grep-and-audit would be prudent.
- **Unit test coverage for LSPRimaging** — most tests cover the shared packages and
  singleLSPR app; LSPRimaging processing functions have limited test coverage.
- **OME-Zarr performance** — large datasets can be slow due to chunk size mismatches.
  An auto-rechunk step on load is not yet implemented.
- **Calibration workflow** — current ruler tool is manual; no automatic pixel-size calibration
  from metadata (e.g., from OME-TIFF metadata fields) yet.
- **Export completeness** — CSV and OME-Zarr export exist; HDF5 export for imaging results
  is not yet implemented.
- **Analysis section state persistence** — analysis is always started as "off" on restore
  (intentional design decision to avoid accidental long computations on startup).

---

## 13. File Format Contracts

### Processing Profile JSON (`*_profile.json`)

```json
{
  "profile_type": "lspr_imaging_processing",
  "profile_version": 3,
  "preprocessing": {
    "image_tools_enabled": true,
    "rotation_angle_deg": 0.0,
    "flip_horizontal": false,
    "flip_vertical": false,
    "crop": { "x": 0, "y": 0, "width": 0, "height": 0, "enabled": false },
    "display_units": "px",
    "scale_bar_visible": false,
    "calibration_enabled": false,
    "microns_per_pixel_x": 1.0,
    "microns_per_pixel_y": 1.0,
    "flatten_background_enabled": false,
    "flatten_background_sigma_px": 48.0,
    "flatten_background_binning": 2,
    "flatten_background_exclude_area_rois": true,
    "flatten_background_exclude_mask": false,
    "local_ring_normalization_enabled": false,
    "chromatic_correction_enabled": false,
    "chromatic_registration_mode": "landmark_radial",
    "chromatic_sample_image_count": 5,
    "chromatic_feature_count": 5,
    "chromatic_subpixel_precision": 4,
    "chromatic_tile_size_px": 96,
    "chromatic_search_radius_px": 24,
    "reference_mode": "auto",
    "reference_wavelength_nm": null,
    "reference_frame_index": 0,
    "histogram_highlight_min_value": null,
    "histogram_highlight_max_value": null
  },
  "area_roi_settings": { /* AreaRoiDetectionSettings fields */ },
  "area_rois": [ /* list of AreaRoi dicts */ ],
  "area_roi_groups": [ /* list of AreaRoiGroup dicts */ ],
  "rois": [ /* list of RoiDefinition dicts */ ],
  "chromatic_models": [ /* list of ChromaticTransformModel dicts */ ],
  "chromatic_landmarks": [ /* list of ChromaticLandmarkObservation dicts */ ],
  "mask_settings": { /* MaskSettings fields, masks as base64 packed bits */ },
  "analysis_cache": { /* keyed by (roi_id, frame_index) tuples */ },
  "session_mask": { "record_path": "...", "mask": { "shape": [...], "data": "base64..." } }
}
```

Backward-compat key aliases handled at load time — see §4.4.

---

*End of LSPR Suite AI Briefing Document.*
*This document was generated by automated codebase audit on 2026-06-29.*
*For the most current source of truth, read the code and AGENTS.md / CLAUDE.md.*
