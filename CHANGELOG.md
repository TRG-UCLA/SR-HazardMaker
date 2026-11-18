# Changes

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
