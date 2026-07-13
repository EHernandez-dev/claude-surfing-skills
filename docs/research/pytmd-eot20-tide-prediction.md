# pyTMD v3.x + EOT20: offline tide-elevation prediction at one lon/lat

Scope: how to predict a tide **elevation** time series (meters) at a single
`(longitude, latitude)` **offline** with pyTMD v3.x using the EOT20 model.
Verified against the pinned release tag **`3.0.9`** of the pyTMD source, the
pyTMD readthedocs (`latest`), and the EOT20 SEANOE dataset page. Primary
sources only; every claim is cited with a URL and (for source claims) a file
path + line range at tag `3.0.9`.

> Big caveat up front: pyTMD v3 is a ground-up rewrite around **xarray**. The
> old v2.x functional low-level API (`pyTMD.io.FES.read_constants` +
> `pyTMD.io.FES.interpolate_constants` + `pyTMD.predict.time_series(t, hc,
> constituents, ...)` + `pyTMD.predict.infer_minor_corrections`) is **gone** in
> 3.0.9. The current low-level path is an xarray-accessor pipeline
> (`m.open_dataset()` → `ds.tmd.interp()` → `ds.tmd.predict()` /
> `ds.tmd.infer()`). Details below.

---

## TL;DR

- **Download**: `https://www.seanoe.org/data/00683/79489/` → zip at
  `https://www.seanoe.org/data/00683/79489/data/85762.zip`, ~2 GB, **CC-BY 4.0,
  no registration**.
- **Directory layout pyTMD expects**: a working `directory/` that contains
  `EOT20/ocean_tides/<CONST>_ocean_eot20.nc` (17 constituent netCDF files).
- **Model short-name**: `"EOT20"` (ocean), `"EOT20_load"` (load). Resolved by
  `pyTMD.io.model(directory).from_database("EOT20")`.
- **High-level call**: `pyTMD.compute.tide_elevations(lon, lat, times,
  directory=..., model="EOT20", crs="4326", standard="datetime")` → returns an
  `xarray.DataArray` **in meters**.
- **Deps** (required): `numpy`, `scipy>=1.10.1`, `pyproj>=2.5.0`,
  `timescale>=0.1.1`, `xarray`, `h5netcdf`, `lxml`, `pint`, `platformdirs`.
  **No `netCDF4`** (uses `h5netcdf`). `matplotlib` is optional.
- **Extrema**: pyTMD does **not** solve for high/low tide analytically. Sample a
  fine time series and use `pyTMD.predict.find_peaks(darr, dim="time")` (returns
  boolean high/low masks) or find local minima/maxima yourself.
- **Land/near-shore**: with `extrapolate=False` (default) off-grid points return
  **NaN** (masked); detect with `numpy.isnan`.

---

## 1. EOT20 model files: source, license, size, layout, registration

**Download / license / size** (SEANOE dataset page,
<https://www.seanoe.org/data/00683/79489/>):
- Download zip: `https://www.seanoe.org/data/00683/79489/data/85762.zip`.
- Total size ~**2 GB**, format NetCDF, resolution 1/8°, coverage 66°S–66°N
  (higher latitudes filled with FES2014b).
- License: **Creative Commons Attribution 4.0 (CC-BY 4.0)**, open access, **no
  registration required**.
- 17 constituents: 2N2, J1, K1, K2, M2, M4, MF, MM, N2, O1, P1, Q1, S1, S2, SA,
  SSA, T2. Both **ocean** and **load** tide atlases are provided.
- DOI: `10.17882/79489` (pyTMD's database entry cites this exact DOI as the
  `reference`; see below).

**How pyTMD names/registers EOT20 and the directory layout it expects.** The
model is defined in the bundled database
`pyTMD/data/database.json` (tag 3.0.9). The `EOT20` (ocean) entry is verbatim:

```json
"EOT20": {
  "format": "FES-netcdf",
  "name": "EOT20",
  "projection": { "datum": "WGS84", "ellps": "WGS84",
                  "lon_wrap": 180, "proj": "longlat", "type": "crs" },
  "reference": "https://doi.org/10.17882/79489",
  "version": "EOT20",
  "z": {
    "model_file": [
      "EOT20/ocean_tides/2N2_ocean_eot20.nc",
      "EOT20/ocean_tides/J1_ocean_eot20.nc",
      "EOT20/ocean_tides/K1_ocean_eot20.nc",
      "EOT20/ocean_tides/K2_ocean_eot20.nc",
      "EOT20/ocean_tides/M2_ocean_eot20.nc",
      "EOT20/ocean_tides/M4_ocean_eot20.nc",
      "EOT20/ocean_tides/MF_ocean_eot20.nc",
      "EOT20/ocean_tides/MM_ocean_eot20.nc",
      "EOT20/ocean_tides/N2_ocean_eot20.nc",
      "EOT20/ocean_tides/O1_ocean_eot20.nc",
      "EOT20/ocean_tides/P1_ocean_eot20.nc",
      "EOT20/ocean_tides/Q1_ocean_eot20.nc",
      "EOT20/ocean_tides/S1_ocean_eot20.nc",
      "EOT20/ocean_tides/S2_ocean_eot20.nc",
      "EOT20/ocean_tides/SA_ocean_eot20.nc",
      "EOT20/ocean_tides/SSA_ocean_eot20.nc",
      "EOT20/ocean_tides/T2_ocean_eot20.nc"
    ],
    "units": "cm",
    "variable": "tide_ocean"
  }
}
```

Source: `pyTMD/data/database.json`, keys `EOT20` and `EOT20_load` (tag 3.0.9),
raw: <https://raw.githubusercontent.com/pyTMD/pyTMD/3.0.9/pyTMD/data/database.json>.

Consequences for setup:
- The `directory` argument you pass to pyTMD is the **parent** folder. pyTMD
  joins it with the relative `model_file` paths, so it must contain a subfolder
  `EOT20/ocean_tides/` holding the 17 `*_ocean_eot20.nc` files. (Load tides live
  under `EOT20/load_tides/<CONST>_load_eot20.nc` and are model `"EOT20_load"`.)
  The SEANOE zip already unpacks to this `EOT20/ocean_tides` + `EOT20/load_tides`
  structure, so unzip it inside your `directory` and point pyTMD at that
  `directory`.
- `format: "FES-netcdf"` → EOT20 is read by pyTMD's **FES netCDF** reader
  (`pyTMD.io.FES`), NOT OTIS/ATLAS. (Confirmed in `open_dataset`, §2.)
- `units: "cm"` → the on-disk amplitudes are centimeters; pyTMD converts to
  meters by default (`use_default_units=True`; see §4).
- Resolution: `from_database("EOT20")` returns a model object `m` whose
  `m.format == "FES-netcdf"`, so `m.corrections == "FES"` (the nodal-correction
  scheme; see §2), because the `corrections` property returns the part of the
  format string before the hyphen. Source:
  `pyTMD/io/model.py:361-369` (`def corrections`),
  <https://raw.githubusercontent.com/pyTMD/pyTMD/3.0.9/pyTMD/io/model.py>.

---

## 2. Precise call sequence to compute elevation (meters) at one point

### 2a. High-level path: `pyTMD.compute.tide_elevations`

Exact signature (source `pyTMD/compute.py:253-270`, tag 3.0.9,
<https://raw.githubusercontent.com/pyTMD/pyTMD/3.0.9/pyTMD/compute.py>):

```python
pyTMD.compute.tide_elevations(
    x: np.ndarray,
    y: np.ndarray,
    delta_time: np.ndarray,
    directory: str | pathlib.Path | None = _default_directory,
    model: str | None = None,
    definition_file: str | pathlib.Path | IOBase | None = None,
    crop: bool = False,
    bounds: list | np.ndarray | None = None,
    buffer: int | float = 0,
    crs: str | int = 4326,
    epoch: list | tuple = (2000, 1, 1, 0, 0, 0),
    type: str | None = "trajectory",
    standard: str = "UTC",
    method: str = "linear",
    extrapolate: bool = False,
    cutoff: int | float = 10.0,
    **kwargs,   # corrections, constituents, infer_minor, minor_constituents,
                # append_node, apply_flexure, chunks
)
```

Docstring states the return is **`xarray.DataArray` — Predicted tide elevation
(meters)** (`pyTMD/compute.py`, Returns section of `tide_elevations`).

Argument notes (all from the `tide_elevations` docstring, `compute.py`):
- `x`, `y`: coordinates in `crs` (default `4326` = WGS84 lon/lat). For a single
  point pass scalars (lon, lat).
- `delta_time`: time coordinates. **Two ways to express time**, selected by
  `standard`:
  - `standard="datetime"` → `delta_time` is a **numpy datetime64 array** (UTC);
    `epoch` is ignored. (This is the idiom used in the official notebook.)
  - `standard` in `{"UTC","GPS","LORAN","TAI"}` → `delta_time` is **numeric
    offsets since `epoch`** (default epoch `(2000,1,1,0,0,0)`), i.e. "delta
    times". Internally converted with `timescale.from_deltatime(delta_time,
    epoch=epoch, standard=standard)` (`compute.py`, the `else` branch of the
    standard check).
- `type`: `"trajectory"` (default; drift/altimetry — scalar location + time
  array works and yields a time series, as the notebook shows), `"grid"`, or
  `"time series"` (single point time sequence). Any of these produce a per-time
  series for a single point.
- `model="EOT20"` selects the ocean model from the database. `directory` is the
  parent folder (see §1).
- `extrapolate` (default `False`), `cutoff` (km, default 10.0), `method`
  (`"linear"`/`"nearest"`) control spatial interpolation/extrapolation.
- `**kwargs`: `corrections` (default `None` → uses `m.corrections`, i.e.
  `"FES"` for EOT20), `constituents` (default all), `infer_minor` (default
  `True`), `minor_constituents` (default `None` → `m.minor`, default set),
  `append_node`, `apply_flexure`.

**What `tide_elevations` does internally** (the authoritative pipeline, from the
body at `pyTMD/compute.py:253-452`) — this IS the low-level sequence:

1. `m = pyTMD.io.model(directory).from_database(model)` (or `.from_file(
   definition_file)`).
2. `ds = m.open_dataset(group="z", chunks=..., append_node=...)` — opens the FES
   netCDF constituent files as an `xarray.Dataset`, converts cm→m
   (`use_default_units=True`).
3. `X, Y = ds.tmd.coords_as(x, y, type=type, time=delta_time, crs=crs)` —
   transforms input coords into the model CRS as DataArrays.
4. Build a timescale: `ts = timescale.from_datetime(delta_time)` if
   `standard=="datetime"`, else `ts = timescale.from_deltatime(delta_time,
   epoch=epoch, standard=standard)`.
5. `nodal_corrections = kwargs["corrections"] or m.corrections` → `"FES"` for
   EOT20. `deltat`: for FES it uses interpolated `ts.tt_ut1` (TT−UT1); for
   OTIS/ATLAS/TMD3/netcdf it is zeroed to match TMDv2.5. (`compute.py`, the
   `if nodal_corrections in (...)` block.)
6. `local = ds.tmd.interp(X, Y, method=method, extrapolate=extrapolate,
   cutoff=cutoff)` — spatial interpolation to the point.
7. `tpred = local.tmd.predict(ts.tide, deltat=deltat,
   corrections=nodal_corrections)` — major-constituent sum.
8. If `infer_minor` (default True): `tinfer = local.tmd.infer(ts.tide,
   deltat=deltat, corrections=nodal_corrections, minor=minor_constituents)`;
   `tpred += tinfer`.
9. Returns `tpred` (meters) with attrs `nodal_corrections`, `inferred`.

Note `ts.tide` is **days relative to 1992-01-01T00:00:00 UTC** (the tide epoch;
`predict.time_series` converts with `_mjd_tide = 48622.0`, the MJD of
1992-01-01). Source: `pyTMD/predict/ocean_load.py:141` (`_mjd_tide = 48622.0`)
and `:143-171` (`def time_series` docstring: "t: Days relative to
1992-01-01T00:00:00"),
<https://raw.githubusercontent.com/pyTMD/pyTMD/3.0.9/pyTMD/predict/ocean_load.py>.

### 2b. Low-level path (xarray accessors) — v3.0.9 form

The functions live in `pyTMD/predict/ocean_load.py` but are re-exported as
`pyTMD.predict.*` (via `pyTMD/predict/__init__.py`). Relevant signatures
(tag 3.0.9):

- `pyTMD.predict.time_series(t, ds, **kwargs)` — `t`: days since
  1992-01-01T00:00:00; `ds`: `xarray.Dataset` of harmonic constants; `kwargs`
  forwarded to `pyTMD.constituents.arguments` (notably `corrections`, default
  `"OTIS"`; pass `corrections="FES"` for EOT20). Returns `xarray.DataArray`.
  (`ocean_load.py:143-216`.)
- `pyTMD.predict.infer_minor(t, ds, **kwargs)` — infers minor constituents;
  `kwargs`: `corrections`, `minor` (list, or None → default set), `deltat`.
  (`ocean_load.py:217+`.)
- Dataset accessors that wrap them:
  `ds.tmd.predict(t, **kwargs)` → calls `pyTMD.predict.time_series`
  (`pyTMD/io/dataset.py:1043-1064`);
  `ds.tmd.infer(t, **kwargs)` → calls `pyTMD.predict.infer_minor`
  (`pyTMD/io/dataset.py:830-850`).
  Source: <https://raw.githubusercontent.com/pyTMD/pyTMD/3.0.9/pyTMD/io/dataset.py>.

**Which reader for EOT20 / how constituents are read.** In
`pyTMD.io.model.open_dataset` (`pyTMD/io/model.py:826-905`), format
`"FES-netcdf"` dispatches to `pyTMD.io.FES.open_mfdataset(model_file,
format=self.file_format, **kwargs)`. `pyTMD/io/FES.py` (tag 3.0.9) exposes
`open_mfdataset`, `open_fes_dataset`, `open_fes_ascii`, `open_fes_netcdf`,
`open_fes_native` (there is **no** `read_constants`/`interpolate_constants` — the
v2.x functional readers were removed).
Sources: `pyTMD/io/model.py:880-905`, `pyTMD/io/FES.py:83-360`,
<https://raw.githubusercontent.com/pyTMD/pyTMD/3.0.9/pyTMD/io/FES.py>.

**Nodal corrections + minor constituents.** Nodal amplitude/phase corrections
`(pu, pf, G)` come from `pyTMD.constituents.arguments(MJD, constituents,
corrections="FES", ...)`; for non-OTIS models (EOT20) the phase angle uses the
astronomical arguments `G`, i.e. `theta = radians(G) + pu`, then the tide is the
`f`-scaled real part summed over constituents (`ocean_load.py:170-205`). Minor
constituents are added by `infer_minor` (default on).

### Minimal runnable snippet (high-level; adapted verbatim from the official Compute-Tides notebook)

The official example (`doc/source/getting_started/Compute-Tides.ipynb`, tag
3.0.9) uses model `"GOT4.10_nc"`; swap to `"EOT20"` and set `directory`:

```python
import pyTMD
import timescale
import numpy as np

# single point (WGS84 lon/lat). EOT20 uses lon_wrap=180 => lon in -180..180.
lon = -9.1                       # e.g. off Portugal
lat = 38.7

# a fine, hourly time axis as numpy datetime64 (UTC)
time = timescale.time.date_range("2026-07-13", "2026-07-15", 1, "h")

tide_h = pyTMD.compute.tide_elevations(
    lon,
    lat,
    time,
    directory="/path/to/tide_models",   # contains EOT20/ocean_tides/*.nc
    model="EOT20",
    crs="4326",
    standard="datetime",                # => `time` is datetime64; epoch ignored
    method="linear",
    extrapolate=False,                  # off-grid/land -> NaN (see gotchas)
    infer_minor=True,                   # add minor constituents (default)
)
# tide_h is an xarray.DataArray in METERS relative to mean tide level
tide_h = tide_h.assign_coords(time=time)

heights_m = np.asarray(tide_h.values)   # meters, NaN where masked/off-grid
```

For the delta-time (non-datetime) style instead of datetime64:

```python
# seconds since 2000-01-01 example
delta_time = np.arange(0, 2*86400, 3600, dtype="f8")   # hourly, 2 days
tide_h = pyTMD.compute.tide_elevations(
    lon, lat, delta_time,
    directory="/path/to/tide_models", model="EOT20",
    epoch=(2000, 1, 1, 0, 0, 0), standard="UTC",   # numeric offsets vs epoch
)
```

Source of the verbatim notebook cell (model name changed to EOT20 + directory
added): <https://raw.githubusercontent.com/pyTMD/pyTMD/3.0.9/doc/source/getting_started/Compute-Tides.ipynb>.

---

## 3. Minimal pip deps + minimum version for offline EOT20 elevation

**Required dependencies** (from `[project].dependencies`,
`pyproject.toml` tag 3.0.9,
<https://raw.githubusercontent.com/pyTMD/pyTMD/3.0.9/pyproject.toml> — mirrored
on `main`):

```
h5netcdf
lxml
numpy
pint
platformdirs
pyproj>=2.5.0
scipy>=1.10.1
timescale>=0.1.1
xarray
```

Notes:
- **`netCDF4` is NOT a dependency.** pyTMD reads netCDF via **`h5netcdf` + `xarray`**.
- `timescale` (Anthropic-of-tides author's companion package) is **required** and
  does the UTC/GPS/TT time handling.
- `pyproj` is required (CRS handling), `scipy` for interpolation.
- `matplotlib`, `cartopy`, `pandas`, `jplephem`, etc. are **optional** (extras
  `all`); not needed for offline elevation prediction.
- `requires-python = "~=3.9"` (Python 3.9–3.14).

So a minimal install is simply: `pip install pyTMD` (pulls the 9 required deps).

**Minimum pyTMD version for EOT20**: EOT20 is present in the current v3.x
database and fully supported by the v3.x API documented here (the xarray-accessor
pipeline requires **pyTMD ≥ 3.0.0**; this file is verified against **3.0.9**).
EOT20 also existed in v2.x under the older functional API. The exact release
that first added EOT20 was not verified against a primary source (see
"Unverified" below).

---

## 4. Datum: what EOT20 heights are relative to

- `tide_elevations` returns the **ocean tide elevation in meters** (docstring),
  i.e. the tidal variation about the model's mean state — **not** a chart datum
  (LAT/MLLW) and **not** an absolute geoid/ellipsoid height. It is the harmonic
  ocean tide, effectively relative to **mean sea level / mean tide level**.
  Source: `tide_elevations` Returns docstring ("Predicted tide elevation
  (meters)"), `pyTMD/compute.py`.
- Unit conversion cm→m: `open_dataset` sets units from the database
  (`units: "cm"` for EOT20) and, with `use_default_units=True` (default), calls
  `ds.tmd.to_default_units()` so the returned DataArray is in **meters**.
  Source: `pyTMD/io/model.py:826-905` (`open_dataset`, `to_default_units`),
  and `pyTMD/io/dataset.py:1154` (`def to_default_units`).
- The pyTMD docs and the SEANOE page do **not** state an explicit tidal datum
  string for EOT20. Treat outputs as tide height about mean sea level; if you
  need heights on a chart datum you must apply your own datum offset. (Datum
  framing flagged as partially unverified below.)

---

## 5. Gotchas

**Land / near-shore → NaN (masked).** Spatial interpolation is
`ds.tmd.interp(X, Y, method=..., extrapolate=False, cutoff=10.0)`. With
`extrapolate=False` (the default), a point off the model grid (land / dry cell)
yields **NaN**, and the constituent sum in `predict.time_series` uses
`skipna=False`, so NaN propagates to the output. Detect with
`numpy.isnan(tide_h.values)`. To fill coastal gaps, set `extrapolate=True` (uses
nearest-neighbor / inverse-distance weighting out to `cutoff` km). Sources:
`pyTMD/io/dataset.py:880-943` (`interp`, defaults `extrapolate=False`),
`pyTMD/predict/ocean_load.py:170-205` (sum uses `skipna=False`),
`pyTMD/compute.py:253-330` (`extrapolate`, `cutoff` args).

**Longitude convention.** EOT20's database entry sets
`"projection": { "lon_wrap": 180, ... }`. `lon_wrap: 180` means the model is
served/handled on the **-180..180** convention. Pass WGS84 lon/lat with
`crs="4326"`; pyTMD's `coords_as` transforms into the model CRS and pyTMD's FES
handling applies the wrap, so passing lon in `-180..180` is the safe convention.
(Passing 0–360 relies on internal wrapping; prefer -180..180 to match
`lon_wrap=180`.) Source: `database.json` EOT20 `projection.lon_wrap`;
`pyTMD/io/model.py` `crs` property (`:399-405`) and `coords_as`
(`pyTMD/io/dataset.py:128-158`).

**High/low tide extrema — NOT provided analytically.** pyTMD does not return
high/low tide times or a slack-water solver. You must **sample a fine time
series and find local minima/maxima**. pyTMD provides a helper for the
peak-finding step: `pyTMD.predict.find_peaks(darr, dim="time", **kwargs)` which
differentiates along `dim`, finds derivative sign changes, and returns a tuple
`(high_peaks, low_peaks)` of **boolean** DataArrays marking peak locations.
Source: `pyTMD/predict/ocean_load.py:1611-1647` (`def find_peaks`).

Example extrema idiom:

```python
from pyTMD.predict import find_peaks
# tide_h: DataArray with a 'time' coord (datetime64), sampled e.g. every 5 min
highs, lows = find_peaks(tide_h, dim="time")
high_tides = tide_h.where(highs, drop=True)   # heights + times of high tides
low_tides  = tide_h.where(lows,  drop=True)
```

Sample densely (a few minutes) for accurate extrema; `find_peaks` only marks the
sampled point nearest each turning point (no sub-sample refinement).

---

## Claims I could NOT verify against a primary source

1. **Exact minimum pyTMD version that first added EOT20.** EOT20 is in the v3.x
   database and works with the v3.x API here (verified at 3.0.9); it also existed
   in v2.x with the old functional API. The specific first release is not
   confirmed from a primary source in this pass.
2. **Precise total download size.** The SEANOE page (via fetch) reports ~2 GB
   total for the dataset; I did not byte-verify the `85762.zip` size, and that
   figure appears to cover both ocean and load atlases.
3. **Explicit tidal datum string for EOT20.** No primary pyTMD/SEANOE page
   states a named datum. The "relative to mean sea level / mean tide level"
   framing is the standard interpretation of a harmonic ocean-tide model and is
   consistent with pyTMD returning "tide elevation (meters)", but a primary
   source that literally names the datum was not found within the three allowed
   sources. (The EOT20 paper, Hart-Davis et al. 2021, ESSD, doi:10.5194/essd-13-3869-2021,
   is the authority but was outside the allowed source set.)
4. **0–360 vs -180..180 auto-wrapping behavior at runtime.** Inferred from
   `lon_wrap=180` in the database and the CRS/coords machinery; not exercised
   with a live run in this pass. Prefer -180..180.

---

### Source index

- EOT20 dataset (download, license, size, constituents):
  <https://www.seanoe.org/data/00683/79489/>
- pyTMD `compute.tide_elevations` signature/behavior:
  `pyTMD/compute.py:253-452` @ tag 3.0.9 —
  <https://raw.githubusercontent.com/pyTMD/pyTMD/3.0.9/pyTMD/compute.py>
  (docs: <https://pytmd.readthedocs.io/en/latest/api_reference/compute.html>)
- EOT20 model definition & directory layout:
  `pyTMD/data/database.json` @ 3.0.9 —
  <https://raw.githubusercontent.com/pyTMD/pyTMD/3.0.9/pyTMD/data/database.json>
- Model resolution / `corrections` / `open_dataset` / `crs`:
  `pyTMD/io/model.py:232-905` @ 3.0.9 —
  <https://raw.githubusercontent.com/pyTMD/pyTMD/3.0.9/pyTMD/io/model.py>
- FES reader (EOT20 is `FES-netcdf`):
  `pyTMD/io/FES.py:83-360` @ 3.0.9 —
  <https://raw.githubusercontent.com/pyTMD/pyTMD/3.0.9/pyTMD/io/FES.py>
- xarray accessors `coords_as`/`interp`/`predict`/`infer`/`to_default_units`:
  `pyTMD/io/dataset.py` @ 3.0.9 —
  <https://raw.githubusercontent.com/pyTMD/pyTMD/3.0.9/pyTMD/io/dataset.py>
- `predict.time_series` / `infer_minor` / `find_peaks` and the 1992 tide epoch:
  `pyTMD/predict/ocean_load.py` @ 3.0.9 —
  <https://raw.githubusercontent.com/pyTMD/pyTMD/3.0.9/pyTMD/predict/ocean_load.py>
- Dependencies / Python version:
  `pyproject.toml` @ 3.0.9 —
  <https://raw.githubusercontent.com/pyTMD/pyTMD/3.0.9/pyproject.toml>
- Canonical usage snippet:
  `doc/source/getting_started/Compute-Tides.ipynb` @ 3.0.9 —
  <https://raw.githubusercontent.com/pyTMD/pyTMD/3.0.9/doc/source/getting_started/Compute-Tides.ipynb>
