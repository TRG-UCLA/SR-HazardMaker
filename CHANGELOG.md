# Changes

## 0.3.1 (2026-11-19)

- SR-HazardMaker has a DOI: [10.5281/zenodo.24356049](https://doi.org/10.5281/zenodo.24356049), for all versions. It is in
  the citation everywhere it is given, in `CITATION.cff`, and in the record each layer carries.

## 0.3.0 (2026-11-19)

- **Scenario builder.** Describe an earthquake (magnitude, style of faulting, a fault trace, dip and
  width, one Vs30) and SR-HazardMaker computes the shaking with the ground-motion model of Boore,
  Stewart, Seyhan and Atkinson (2014), as the median or shifted by a number of standard deviations.
  On the page ("An earthquake to describe") and on the command line (`sr-hazardmaker scenario`).
- The model was checked against all 600 cases of the OpenQuake engine's verification tables for it;
  69 of them are checked on every change. The page and the command line give the same scenario.

## 0.2.1 (2026-11-19)

- The first release archived on Zenodo, which gives the tool a permanent identifier (DOI).

## 0.2.0 (2026-11-18)

- Licensed under the BSD 3-Clause license: free to use, change and share.
- How to cite the tool: `sr-hazardmaker cite`, a block at the foot of the browser page with copy
  buttons, the guide, and `CITATION.cff`. The Python and browser versions give the same citation.

## 0.1.1 (2026-11-18)

- Names the maker, E. Taciroglu: in the guide, the package details, the page, a `CITATION.cff`, and in
  the `provenance` of every layer written (`made_with`).

## 0.1.0 (2026-11-18)

First release.

- Converters for USGS National Seismic Hazard Model tables (separately or inside the USGS archive),
  USGS ShakeMap `grid.xml` (zipped or not), and tables of points on a regular grid.
- The same conversions in a browser page and as a command-line tool and Python package, tested to
  produce the same layers.
- Checks on every layer, a preview map in the browser, the layer file and its index entry as output.
- A layer cannot be built without a stated source.
