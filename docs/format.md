# The ShakeReady hazard-layer format

`"format": "shakeready-hazard-grid/1"`

A hazard layer is one JSON file holding ground motions on a regular grid of latitude and longitude,
with a description of where they came from. The ShakeReady map reads this format; SR-HazardMaker
writes it.

## Fields

| Field | Meaning |
|---|---|
| `format` | always `shakeready-hazard-grid/1` |
| `id` | identifier, also the file name. Lower-case letters, digits, dots, hyphens, underscores |
| `title` | display name |
| `kind` | `probabilistic` or `scenario` (see below) |
| `description` | free text: how the ground motions were produced |
| `site_condition` | what the values assume about the ground, in words |
| `units` | always `g` |
| `scale` | stored integers times `scale` give the value in g. SR-HazardMaker uses 0.001 |
| `nodata` | the integer that marks a node with no value. SR-HazardMaker uses -1 |
| `grid` | `lat_min`, `lon_min`, `dlat`, `dlon`, `nlat`, `nlon` |
| `default_level` | the `id` of the level a map should select first |
| `levels` | a list; each entry has `id`, `label`, and `measures` |
| `source` | where the values came from (see below) |
| `provenance` | written by the tool: what made the file (`generator`, `made_with`), when, and any processing notes |

### Two kinds

- **`probabilistic`**: a hazard map. Each place is given the shaking that has a set chance of being
  exceeded there, at one or more probability levels (each a `level`). It does not describe one
  earthquake: its values would not all occur together. Results computed from it are per site and
  should not be added up across sites as if they came from a single event.
- **`scenario`**: the ground motions of one earthquake, computed or recorded. It has one `level`.

### The grid

Nodes lie at `lat_min + j * dlat` and `lon_min + i * dlon`, for `j` from 0 to `nlat - 1` and `i`
from 0 to `nlon - 1`. Values are node-registered: each value belongs to a point, not to a cell.
Each array is stored row by row from south to north, each row from west to east, so the value at
node (`j`, `i`) is element `j * nlon + i`.

### Measures

Each level's `measures` holds one array of `nlat * nlon` integers per intensity measure:

| Name | Meaning |
|---|---|
| `SA1P0` | spectral acceleration at 1.0 second, 5% damping. Required |
| `SA0P3` | spectral acceleration at 0.3 second, 5% damping. Optional |
| `PGA` | peak ground acceleration. Optional |

### Source

`source` records where the ground motions came from: `title`, `publisher` (or author), `version`,
`doi`, `url`, `retrieved_utc` (the date they were obtained or made), `contact`. SR-HazardMaker adds
`input_files` (the files it read) and, for a ShakeMap, `event` (identifier, magnitude, depth,
location, time, and whether it was recorded or a scenario).

For a computed scenario SR-HazardMaker adds `scenario`: the magnitude, style of faulting, fault
trace, length, dip and down-dip width, the Vs30, the number of standard deviations from the median
(`epsilon`), the ground-motion model and its options, the horizontal component, and how distance
was measured. These are all the inputs, so the layer can be recomputed from them.

A layer without a source title, publisher and date draws a warning from the checker, and
SR-HazardMaker will not build one.

## Reading a value at a site

The value at a point is found by bilinear interpolation between the four nodes around it. A node
without a value is left out and the remaining nodes' weights are rescaled. A point outside the
grid, or with no valid node around it, has no value.

## Checks

`sr-hazardmaker check layer.json`, and the browser page, report:

- **errors**, which make a layer unusable: a missing field, an unknown kind or measure, an array
  whose length does not match the grid, a level without `SA1P0`, a measure with no values at all;
- **warnings**, which deserve a second look: no overlap with California, an incomplete source,
  values outside 0 to 10 g (usually a sign of the wrong units).

## Listing a layer

A hazard repository lists its layers in an `index.json`. SR-HazardMaker writes the entry for a
layer alongside it, as `<id>.index-entry.json`: the layer's id, title, kind, file path, site
condition, measures, levels, source, checksum (SHA-256) and size.
