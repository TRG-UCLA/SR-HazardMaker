# SR-HazardMaker

Made by **E. Taciroglu**, University of California, Los Angeles.

SR-HazardMaker makes **hazard layers** for the
[ShakeReady](https://trg-ucla.github.io/ShakeReady/) map: gridded shaking that the map can display
and use to estimate damage to bridges and buildings. It reads the files that ground motions
usually come in, or computes the shaking of an earthquake you describe, checks the result, and
writes one small file in a documented format, with a record of where the values came from.

It is part of ShakeReady, a collaboration of universities and research centers that publishes
tools and data for quantifying and improving seismic resilience.

## Three ways to use it

**In a browser.** Open <https://trg-ucla.github.io/SR-HazardMaker/>, choose what you have, and add
the file. Files are read on your own computer; nothing is uploaded. The page checks the result,
draws it on a map, and gives you the layer file.

**From the command line.**

```
pip install git+https://github.com/TRG-UCLA/SR-HazardMaker

sr-hazardmaker nshm conus_2023.R2-MPRS-scBC-vs760-0p05.zip
sr-hazardmaker shakemap grid.xml
sr-hazardmaker table simulation.csv --sa10 "SA(1.0)" --units cm/s2 \
    --title "My scenario" --site "As computed, site-specific" \
    --source-title "Simulation of the ... earthquake" --publisher "A. Researcher"
sr-hazardmaker scenario --magnitude 7 --mechanism strike-slip \
    --from 34.40,-118.80 --to 34.10,-118.30 --publisher "A. Researcher"
sr-hazardmaker check my-layer.json
sr-hazardmaker cite
```

Python 3.9 or later; no other dependencies. Without installing, run it from a copy of this
repository as `PYTHONPATH=src python -m sr_hazardmaker ...`.

**From Python.**

```python
from sr_hazardmaker import grid, shakemap

text, name = shakemap.read_text("grid.xml")
parsed = shakemap.parse(text, name)
meta = shakemap.defaults(parsed)                     # title, identifier, source, read from the file
meta["source"]["retrieved_utc"] = "2026-11-18"
layer = shakemap.build(parsed, meta, name)
errors, warnings = grid.check(layer)
value = grid.value_at(layer, layer["levels"][0]["measures"]["SA1P0"], 34.05, -118.25)   # in g
```

## What goes in

| Input | Notes |
|---|---|
| **USGS National Seismic Hazard Model** | The archive for one site condition, or the `map.csv` tables for SA1P0, SA0P3 and PGA from inside it. The current release is [doi:10.5066/P14VGAV4](https://doi.org/10.5066/P14VGAV4). The archive is large (about 2 GB); only the three tables needed are read from it. The result is cropped to California and its surroundings. |
| **USGS ShakeMap** | The `grid.xml` of a recorded or scenario earthquake, zipped or not. Field names and units are read from the file, since both have changed between versions of the ShakeMap software. |
| **A table of points** | A CSV with longitude, latitude and spectral acceleration at 1.0 second, and optionally at 0.3 second and peak ground acceleration, in g, %g, cm/s² or m/s². The points must form an evenly spaced grid. Scattered points are not supported yet. |

## Describing an earthquake: the scenario builder

With no file at all, SR-HazardMaker can compute the shaking of an earthquake you describe:

- **the earthquake:** its magnitude and style of faulting (strike-slip, reverse, normal, or unspecified);
- **the rupture:** a straight fault trace between two points, a dip, and for a dipping fault a
  down-dip width. A dipping fault dips to the right of the direction from the first point to the second;
- **the ground:** one Vs30 used everywhere (760 m/s, the reference rock condition, unless you say otherwise);
- **the level:** the median, or the median shifted by a number of standard deviations.

The shaking is computed with the ground-motion model of Boore, Stewart, Seyhan and Atkinson (2014),
*Earthquake Spectra* 30(3), 1057-1085: its base model, with no regional adjustment and no
basin-depth term, for the RotD50 horizontal component. It applies to magnitudes 3 to 8.5 (to 7 for
normal faulting) and Vs30 from 150 to 1500 m/s, and the tool refuses inputs outside that.

What a scenario layer is not:

- It does not account for local soil: one Vs30 is used everywhere.
- It is not a recording, and with a level other than the median it is not a simulated realization
  of the event either: every point is shifted by the same number of standard deviations.
- The rupture is a flat rectangle. Distances are measured in a flat local frame, which is accurate
  to well under one percent over the few hundred kilometers a scenario covers.

Every input is written into the layer (`source.scenario`), so a scenario can be reproduced exactly.

## What comes out

- `<id>.json`, the layer, in the format described in [docs/format.md](docs/format.md);
- `<id>.index-entry.json`, the layer's entry for a hazard repository's index, with its checksum.

Every layer is checked before it is written: structure, units, coverage, and whether it says where
it came from.

## A layer must say where it comes from

SR-HazardMaker will not build a layer without a title, a ground condition, a source title, a
publisher or author, and a date. It fills these in itself only when the file identifies them, as a
ShakeMap does. This is deliberate: a damage estimate is only as traceable as the ground motions
behind it, and every table the ShakeReady map produces cites the layer it used.

## Using a layer on the ShakeReady map

- **Straight away, on your own computer.** On the map, under Hazard layer, choose "Use a layer file
  from my computer". On the ShakeReady site's own copy of SR-HazardMaker there is also a button
  that keeps the layer in your browser and opens the map on it. Either way the layer stays on your
  computer, is marked as yours and unpublished, and any damage table made from it says so.
- **For everyone.** Send the layer file and its index entry to the ShakeReady maintainers, or open
  a pull request adding them to the
  [ShakeReady hazard repository](https://github.com/TRG-UCLA/ShakeReady/tree/main/hazards). A layer
  is reviewed before it is listed.

## How it is tested

Run the tests from the repository root:

```
python -m unittest discover -s tests -v
```

- Each converter is checked value by value against the file it read, including unit conversion,
  rows given in any order, and cells left empty.
- The Python converters and the browser converters are run on the same inputs and must produce the
  same layer, and the two implementations of reading a value at a point must agree exactly at
  thousands of random points. These tests need Node.js 20 or later and are skipped without it.
- An archive must give the same layer as its tables given separately, including in ZIP64 form.
- All ground-motion values in the tests are made up inside the test file.

Before release 0.1.0 the ShakeMap converter was also run on seven real USGS ShakeMap files from
three versions of the ShakeMap software (shortened samples in the OpenQuake engine's test data),
which is how the two spellings of percent-g, `pctg` and `%g`, came to be handled. The browser page
was tested with a 290 MB archive, and a layer made with it was used on the ShakeReady map, where
the damage table matched an independent calculation for all 25,974 California bridges.

The ground-motion model was written from the published equations and checked against all 600
cases of the verification tables distributed with the OpenQuake engine for this model: 1,800
medians agree to within 5 parts per million and 1,800 standard deviations to within 0.00005, the
precision of the tables. A subset of 69 cases is checked on every change. The scenario builder's
fault geometry is tested against hand-computed distances, and the page and the command line must
produce the same scenario layer, value for value.

One thing has not been tested: the real USGS hazard-model archive, which the tool was built for
from a description of its layout. If its folder or column names differ, SR-HazardMaker reports
what it found instead of guessing.

## Choices and limits

The choices made in converting (precision, cropping, units, what counts as a regular grid) are
recorded with their reasons on ShakeReady's
[Methods page](https://trg-ucla.github.io/ShakeReady/methods.html#hazardmaker), as HM-01 to HM-08.
In brief: values are stored to 0.001 g; USGS hazard-model tables are cropped to latitude 32.0 to
42.5 north and longitude 125.0 to 113.5 west; a ShakeMap is kept whole and labeled site-specific;
ground motions are used as given, with no adjustment for local soil.

## Status

Version 0.3.0. Next for the scenario builder: more ground-motion models, choosing the fault on a
map, and ground conditions that vary from place to place.

## Who made this

SR-HazardMaker was made by E. Taciroglu (Taciroglu Research Group, University of California, Los
Angeles) for the ShakeReady project. The code was written with an AI assistant (Claude, made by
Anthropic) under his direction, and is covered by the tests described above.

Every layer the tool writes names the tool and its maker in its `provenance`.

## Free to use, and how to cite it

SR-HazardMaker is free for anyone to use, change and share, including in commercial work, under
the [BSD 3-Clause license](LICENSE). The license asks only that the copyright notice stays with
copies of the code, and that the maker's name is not used to endorse things built from it.

If SR-HazardMaker helps your work, please cite it:

> Taciroglu, E. (2026). *SR-HazardMaker* (Version 0.3.0) [Computer software].
> https://github.com/TRG-UCLA/SR-HazardMaker

```bibtex
@software{taciroglu_sr_hazardmaker,
  author  = {Taciroglu, E.},
  title   = {{SR-HazardMaker}},
  year    = {2026},
  version = {0.3.0},
  url     = {https://github.com/TRG-UCLA/SR-HazardMaker}
}
```

The citation is also available from GitHub's "Cite this repository" button (from `CITATION.cff`),
from the command line (`sr-hazardmaker cite`), and at the foot of the browser page. Cite the
version you used: it is printed by `sr-hazardmaker --version` and written into every layer.
