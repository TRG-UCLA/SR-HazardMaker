"""Tests for SR-HazardMaker. Run from the repository root:

    python -m unittest discover -s tests -v

Every ground-motion value used here is made up inside this file. Nothing here is a hazard estimate.
The tests that compare the Python converters with the browser ones need Node.js 20 or later and
are skipped without it.
"""
import csv, io, json, math, os, random, shutil, subprocess, sys, tempfile, unittest, zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
import sr_hazardmaker                                              # noqa: E402
from sr_hazardmaker import bssa14, cli, grid, nshm, scenario, shakemap, table   # noqa: E402

NODE = shutil.which("node")
META = {"id": "test-layer", "title": "Test layer", "site_condition": "Test condition", "description": "Made up.",
        "source": {"title": "Test source", "publisher": "Tester", "retrieved_utc": "2026-01-01"}}


def field(lat, lon):
    return 0.08 + 0.9 * math.exp(-((lat - 34.2) ** 2 + (lon + 118.3) ** 2) / 2.9) + 0.6 * math.exp(-((lat - 37.8) ** 2 + (lon + 122.2) ** 2) / 1.3)


def read(path, mode="r"):
    with open(path, mode) as f:
        return f.read()


def dump(obj, path):
    with open(path, "w") as f:
        json.dump(obj, f)


def strip(layer):
    """A layer without the two fields that legitimately differ between tools and runs."""
    out = json.loads(json.dumps(layer))
    out["provenance"].pop("generated_utc", None)
    out["provenance"].pop("generator", None)
    return out


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="srhm-")
        t = cls.tmp
        # --- USGS-style hazard-map tables: a region wider than the crop, 0.1 degree spacing, some empty cells
        cls.truth = {}
        lats = [round(31.0 + 0.1 * j, 1) for j in range(131)]
        lons = [round(-127.0 + 0.1 * i, 1) for i in range(151)]
        mult = {"SA1P0": lambda lo: 1.0, "SA0P3": lambda lo: 2.0 + 1.5 * (lo + 125) / 11, "PGA": lambda lo: 0.45 * (2.0 + 1.5 * (lo + 125) / 11)}
        cls.tables = {}
        for m in ("SA1P0", "SA0P3", "PGA"):
            path = os.path.join(t, f"{m}_map.csv")
            with open(path, "w", newline="") as f:
                w = csv.writer(f, lineterminator="\n")
                w.writerow(["lon", "lat", "475.0", "975.0", "2475.0"])
                for la in lats:
                    for lo in lons:
                        if lo < -124.6 and la < 36:
                            w.writerow([lo, la, "", "", ""])
                            continue
                        v = field(la, lo) * mult[m](lo)
                        w.writerow([lo, la, f"{0.65 * v:.6f}", f"{v:.6f}", f"{1.5 * v:.6f}"])
                        cls.truth[(m, la, lo)] = float(f"{v:.6f}")
            cls.tables[m] = path
        root = "conus_2023.R2-MPRS-scBC-vs760-0p05/vs30-760"

        def archive(name, zip64):
            path = os.path.join(t, name)
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
                for imt in ("PGA", "PGV", "SA0P3", "SA1P0", "SA2P0"):
                    data = read(cls.tables.get(imt, cls.tables["SA1P0"]), "rb")
                    for member, payload in ((f"{root}/{imt}/curves.csv", b"lon,lat,0.001\n" * 50), (f"{root}/{imt}/map.csv", data)):
                        with z.open(zipfile.ZipInfo(member), "w", force_zip64=zip64) as f:
                            f.write(payload)
                z.writestr(f"{root}/README.md", "test archive")
            return path
        cls.zip = archive("conus_2023.R2-MPRS-scBC-vs760-0p05.zip", False)
        cls.zip64 = archive("zip64_conus_2023.R2-MPRS-scBC-vs760-0p05.zip", True)

        # --- a ShakeMap grid in the layout of the USGS files
        cls.sm_truth = {}
        nlon, nlat, lon0, lat1, d = 61, 49, -119.5, 35.0, 1 / 60

        def grid_xml(name, unit, order, shuffle=False):
            rows = []
            for j in range(nlat):
                for i in range(nlon):
                    lat, lon = lat1 - j * d, lon0 + i * d
                    r = math.hypot((lat - 34.6) / 0.2, (lon + 119.0) / 0.25)
                    vals = {"PSA10": 2 + 95 * math.exp(-r * r / 2)}
                    vals["PSA03"] = vals["PSA10"] * (1.6 + 0.4 * math.cos(lon)); vals["PGA"] = vals["PSA03"] * 0.42
                    vals.update(MMI=5.0, PGV=vals["PSA10"] * 0.9, PSA30=vals["PSA10"] * 0.3, SVEL=400.0)
                    txt = {k: f"{v:.4g}" for k, v in vals.items()}
                    rows.append(f"{lon:.4f} {lat:.4f} " + " ".join(txt[k] for k in order))
                    cls.sm_truth[(round(lat, 4), round(lon, 4))] = tuple(float(txt[k]) for k in ("PSA10", "PSA03", "PGA"))
            if shuffle:
                random.Random(3).shuffle(rows)
            fields = "".join(f'<grid_field index="{n + 3}" name="{k}" units="{unit if k.startswith(("PSA", "PGA")) else "x"}" />\n' for n, k in enumerate(order))
            head = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<shakemap_grid xmlns="http://earthquake.usgs.gov/eqcenter/shakemap" event_id="testevent01" '
                    f'shakemap_id="testevent01" shakemap_version="3" code_version="4.1.5" process_timestamp="2026-11-01T00:00:00Z" shakemap_originator="xx" map_status="RELEASED" shakemap_event_type="SCENARIO">\n'
                    f'<event event_id="testevent01" magnitude="6.7" depth="18.2" lat="34.600000" lon="-119.000000" event_timestamp="2026-01-17T12:30:55" event_network="xx" event_description="MADE-UP TEST &amp; EVENT" />\n'
                    f'<grid_specification lon_min="{lon0:.4f}" lat_min="{lat1 - (nlat - 1) * d:.4f}" lon_max="{lon0 + (nlon - 1) * d:.4f}" lat_max="{lat1:.4f}" nominal_lon_spacing="0.0167" nominal_lat_spacing="0.0167" nlon="{nlon}" nlat="{nlat}" />\n'
                    f'<event_specific_uncertainty name="pga" value="0.000000" numsta="" />\n<grid_field index="1" name="LON" units="dd" />\n<grid_field index="2" name="LAT" units="dd" />\n{fields}<grid_data>\n')
            path = os.path.join(t, name)
            with open(path, "w") as f:
                f.write(head + "\n".join(rows) + "\n</grid_data>\n</shakemap_grid>\n")
            return path
        cls.sm_new = grid_xml("grid.xml", "%g", ["MMI", "PGA", "PGV", "PSA03", "PSA10", "PSA30", "SVEL"])
        cls.sm_old = grid_xml("grid_old.xml", "pctg", ["PGA", "PGV", "MMI", "PSA03", "PSA10", "PSA30", "SVEL"])
        cls.sm_shuffled = grid_xml("grid_shuffled.xml", "%g", ["MMI", "PGA", "PGV", "PSA03", "PSA10", "PSA30", "SVEL"], shuffle=True)
        cls.sm_badunit = grid_xml("grid_badunit.xml", "furlongs", ["MMI", "PGA", "PGV", "PSA03", "PSA10", "PSA30", "SVEL"])
        cls.sm_zip = os.path.join(t, "grid.xml.zip")
        with zipfile.ZipFile(cls.sm_zip, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(cls.sm_new, "grid.xml")

        # --- a plain table in cm/s^2, own column names, rows in random order
        cls.tb_truth = {}
        pts = []
        for j in range(21):
            for i in range(31):
                lat, lon = 33.5 + 0.05 * j, -119.0 + 0.05 * i
                g = 0.05 + 0.9 * math.exp(-((lat - 34.05) ** 2 + (lon + 118.25) ** 2) / 0.08)
                pts.append((lon, lat, g))
        random.Random(5).shuffle(pts)
        cls.tb = os.path.join(t, "simulation_output.csv")
        with open(cls.tb, "w", newline="") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(["Longitude", "Latitude", "PGA (cm/s2)", "SA(0.3)", "SA(1.0)"])
            for lon, lat, g in pts:
                row = [f"{lon:.3f}", f"{lat:.3f}", f"{0.5 * g * 980.665:.3f}", f"{1.4 * g * 980.665:.3f}", f"{g * 980.665:.3f}"]
                w.writerow(row)
                cls.tb_truth[(float(row[1]), float(row[0]))] = tuple(float(x) / 980.665 for x in (row[4], row[3], row[2]))
        cls.tb_scattered = os.path.join(t, "scattered.csv")
        with open(cls.tb_scattered, "w") as f:
            f.write("lon,lat,sa1.0\n" + "\n".join(f"{-119 + random.Random(i).random():.4f},{34 + random.Random(i + 99).random():.4f},0.2" for i in range(40)) + "\n")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def nshm_layer(self, paths, meta=None):
        groups, _ = nshm.find(paths)
        label = next(g for g in groups if "SA1P0" in groups[g])
        return nshm.build(groups[label], meta or META), label, groups

    def js(self, command, args):
        a, o = os.path.join(self.tmp, "js_args.json"), os.path.join(self.tmp, "js_out.json")
        dump(args, a)
        subprocess.run([NODE, os.path.join(ROOT, "tests", "js_run.cjs"), command, o, a], check=False, timeout=120)
        return json.loads(read(o))


class TestHazardModel(Base):
    def test_values_match_the_tables(self):
        layer, _, _ = self.nshm_layer(list(self.tables.values()))
        g = layer["grid"]
        self.assertEqual((g["dlat"], g["dlon"]), (0.1, 0.1))
        self.assertEqual([lv["id"] for lv in layer["levels"]], ["rp475", "rp975", "rp2475"])
        self.assertEqual(layer["default_level"], "rp975")
        lv = layer["levels"][1]
        checked = 0
        for (m, la, lo), v in self.truth.items():
            if not (32.0 <= la <= 42.5 and -125.0 <= lo <= -113.5):
                continue
            k = int(grid.rnd((la - g["lat_min"]) / g["dlat"])) * g["nlon"] + int(grid.rnd((lo - g["lon_min"]) / g["dlon"]))
            self.assertAlmostEqual(lv["measures"][m][k] * grid.SCALE, v, delta=0.00051)
            checked += 1
        self.assertGreater(checked, 30000)

    def test_cropped_and_empty_cells_kept_empty(self):
        layer, _, _ = self.nshm_layer(list(self.tables.values()))
        g = layer["grid"]
        self.assertEqual((g["lat_min"], g["lon_min"]), (32.0, -125.0))
        self.assertAlmostEqual(g["lat_min"] + (g["nlat"] - 1) * g["dlat"], 42.5)
        k = int(grid.rnd((33.0 - g["lat_min"]) / g["dlat"])) * g["nlon"] + int(grid.rnd((-124.8 - g["lon_min"]) / g["dlon"]))
        self.assertEqual(layer["levels"][0]["measures"]["SA1P0"][k], grid.NODATA)

    def test_archive_gives_the_same_layer(self):
        ref = strip(self.nshm_layer(list(self.tables.values()))[0])
        for path in (self.zip, self.zip64):
            layer, label, _ = self.nshm_layer([path])
            self.assertEqual(label, "Vs30 = 760 m/s")
            got = strip(layer)
            for key in ("grid", "levels", "default_level"):
                self.assertEqual(got[key], ref[key], path)

    def test_details_read_from_the_archive_name(self):
        _, label, groups = self.nshm_layer([self.zip])
        d = nshm.defaults(label, [self.zip] + [it["name"] for it in groups[label].values()])
        self.assertEqual(d["id"], "usgs-nshm-2023r2-vs760")
        self.assertIn("site class BC", d["site_condition"])
        self.assertEqual(d["source"]["doi"], "10.5066/P14VGAV4")
        plain = nshm.defaults("the files you added", list(self.tables.values()))
        self.assertEqual(plain["source"]["title"], "")          # nothing is guessed when the files do not say

    def test_missing_sa10_is_refused(self):
        groups, _ = nshm.find([self.tables["PGA"]])
        with self.assertRaises(ValueError):
            nshm.build(groups["the files you added"], META)


class TestShakeMap(Base):
    def test_values_units_and_placement(self):
        text, name = shakemap.read_text(self.sm_new)
        p = shakemap.parse(text, name)
        self.assertEqual((p["rows"], p["off"], p["expected"]), (61 * 49, 0, 61 * 49))
        g = p["grid"]
        for (lat, lon), (s10, s03, pga) in self.sm_truth.items():
            k = int(grid.rnd((lat - g["lat_min"]) / g["dlat"])) * g["nlon"] + int(grid.rnd((lon - g["lon_min"]) / g["dlon"]))
            self.assertAlmostEqual(p["measures"]["SA1P0"][k] * grid.SCALE, s10 / 100, delta=0.00051)
            self.assertAlmostEqual(p["measures"]["SA0P3"][k] * grid.SCALE, s03 / 100, delta=0.00051)
            self.assertAlmostEqual(p["measures"]["PGA"][k] * grid.SCALE, pga / 100, delta=0.00051)

    def test_unit_spelling_field_order_row_order_and_zip_do_not_matter(self):
        ref = shakemap.parse(*shakemap.read_text(self.sm_new))
        for path in (self.sm_old, self.sm_shuffled, self.sm_zip):
            p = shakemap.parse(*shakemap.read_text(path))
            self.assertEqual(p["measures"], ref["measures"], path)
            self.assertEqual(p["grid"], ref["grid"], path)

    def test_unknown_unit_is_refused(self):
        with self.assertRaises(ValueError):
            shakemap.parse(*shakemap.read_text(self.sm_badunit))

    def test_event_details(self):
        text, name = shakemap.read_text(self.sm_new)
        p = shakemap.parse(text, name)
        d = shakemap.defaults(p)
        self.assertEqual(d["id"], "shakemap-testevent01-v3")
        self.assertEqual(d["title"], "M 6.7 MADE-UP TEST & EVENT, 2026-01-17 (USGS ShakeMap)")
        layer = shakemap.build(p, dict(META, **{k: d[k] for k in ("id", "title", "site_condition")}), name)
        self.assertEqual(layer["source"]["event"]["magnitude"], 6.7)
        self.assertEqual(layer["source"]["event"]["type"], "scenario")
        self.assertEqual(grid.check(layer)[0], [])

    def test_not_a_shakemap(self):
        with self.assertRaises(ValueError):
            shakemap.parse("<html></html>", "x")


class TestTable(Base):
    def test_shuffled_rows_and_unit_conversion(self):
        t = table.read(read(self.tb))
        self.assertEqual([t["guess"][k] for k in ("lon", "lat", "SA1P0", "SA0P3")], [0, 1, 4, 3])
        mapping = dict(t["guess"], PGA=table.column(t, "PGA (cm/s2)"))
        layer = table.build(t, mapping, "cm/s2", dict(META, file_name="simulation_output.csv"))
        g = layer["grid"]
        self.assertEqual((g["nlat"], g["nlon"]), (21, 31))
        m = layer["levels"][0]["measures"]
        for (lat, lon), (s10, s03, pga) in self.tb_truth.items():
            k = int(grid.rnd((lat - g["lat_min"]) / g["dlat"])) * g["nlon"] + int(grid.rnd((lon - g["lon_min"]) / g["dlon"]))
            self.assertAlmostEqual(m["SA1P0"][k] * grid.SCALE, s10, delta=0.00051)
            self.assertAlmostEqual(m["SA0P3"][k] * grid.SCALE, s03, delta=0.00051)
            self.assertAlmostEqual(m["PGA"][k] * grid.SCALE, pga, delta=0.00051)

    def test_scattered_points_are_refused(self):
        t = table.read(read(self.tb_scattered))
        with self.assertRaises(ValueError):
            table.build(t, t["guess"], "g", META)


class TestFormat(Base):
    def small(self):
        return {"format": grid.FORMAT, "id": "small", "title": "Small", "kind": "scenario", "site_condition": "x", "units": "g", "scale": 0.001, "nodata": -1,
                "grid": {"lat_min": 34.0, "lon_min": -119.0, "dlat": 0.5, "dlon": 0.5, "nlat": 3, "nlon": 3},
                "levels": [{"id": "event", "label": "Event", "measures": {"SA1P0": [100, 200, 300, 400, 500, 600, 700, 800, -1]}}],
                "source": {"title": "t", "publisher": "p", "retrieved_utc": "2026-01-01"}}

    def test_value_at(self):
        h = self.small(); v = h["levels"][0]["measures"]["SA1P0"]
        self.assertAlmostEqual(grid.value_at(h, v, 34.0, -119.0), 0.1)                 # at a node
        self.assertAlmostEqual(grid.value_at(h, v, 34.25, -118.75), (0.1 + 0.2 + 0.4 + 0.5) / 4)   # middle of a cell
        self.assertAlmostEqual(grid.value_at(h, v, 34.75, -118.25), (0.5 + 0.6 + 0.8) / 3)         # one corner has no value
        self.assertIsNone(grid.value_at(h, v, 35.0, -118.0))                            # exactly at the node without a value
        self.assertIsNone(grid.value_at(h, v, 33.9, -119.0))                            # outside
        self.assertIsNone(grid.value_at(h, v, 34.0, -117.9))

    def test_check_finds_problems(self):
        ok = self.small()
        self.assertEqual(grid.check(ok), ([], []))
        bad = self.small(); bad["levels"][0]["measures"] = {"PGA": [1] * 9}
        self.assertTrue(any("Sa(1.0 s)" in e for e in grid.check(bad)[0]))
        bad = self.small(); bad["levels"][0]["measures"]["SA1P0"] = [1, 2, 3]
        self.assertTrue(any("9 nodes" in e for e in grid.check(bad)[0]))
        bad = self.small(); bad["id"] = "Not OK"
        self.assertTrue(grid.check(bad)[0])
        far = self.small(); far["grid"]["lat_min"] = 10.0
        self.assertTrue(any("California" in w for w in grid.check(far)[1]))
        nosrc = self.small(); nosrc["source"] = {}
        self.assertEqual(len(grid.check(nosrc)[1]), 3)
        units = self.small(); units["levels"][0]["measures"]["SA1P0"] = [50000] * 9
        self.assertTrue(any("Check the units" in w for w in grid.check(units)[1]))


# Reference values for the ground-motion model: magnitude, mechanism, Rjb (km), Vs30 (m/s), then the median PGA, Sa(0.3 s)
# and Sa(1.0 s) in g, then their total standard deviations. From the verification tables distributed with the OpenQuake
# engine for Boore et al. (2014) (openquake/hazardlib/tests/gsim/data/BSSA2014). The model was checked against all 600
# rows of those tables before release; this is a subset kept here so the check runs on every change.
REFERENCE = [
    [5.0, "normal", 50.0, 600.0, 0.00839719, 0.0120419, 0.00212687, 0.7022, 0.6852, 0.7109],
    [5.0, "normal", 0.0, 600.0, 0.146535, 0.164149, 0.0265675, 0.7022, 0.6852, 0.7109],
    [5.0, "normal", 0.0, 250.0, 0.193782, 0.243305, 0.0540372, 0.6651, 0.6568, 0.7004],
    [5.0, "normal", 50.0, 250.0, 0.0139057, 0.0244365, 0.00523895, 0.6651, 0.6568, 0.7004],
    [7.0, "normal", 50.0, 600.0, 0.0589322, 0.120621, 0.0434342, 0.6051, 0.6059, 0.6924],
    [7.0, "normal", 50.0, 250.0, 0.0880977, 0.212172, 0.0980611, 0.5694, 0.5767, 0.681],
    [7.0, "normal", 0.0, 600.0, 0.405173, 0.874069, 0.346414, 0.6051, 0.6059, 0.6924],
    [7.0, "normal", 0.0, 250.0, 0.435297, 0.96927, 0.590376, 0.5694, 0.5767, 0.681],
    [4.0, "strike-slip", 20.0, 200.0, 0.00967555, 0.014555, 0.00200089, 0.741, 0.7228, 0.7294],
    [4.0, "strike-slip", 100.0, 400.0, 0.000486204, 0.000859717, 0.000126392, 0.8009, 0.7664, 0.7442],
    [4.0, "strike-slip", 100.0, 760.0, 0.000330915, 0.000501143, 6.44366e-05, 0.8009, 0.7664, 0.7442],
    [4.0, "strike-slip", 20.0, 400.0, 0.00648024, 0.00828976, 0.000980325, 0.8009, 0.7664, 0.7442],
    [4.0, "strike-slip", 0.0, 200.0, 0.0638345, 0.071943, 0.00919164, 0.741, 0.7228, 0.7294],
    [4.0, "strike-slip", 0.0, 760.0, 0.0325393, 0.0278926, 0.00253427, 0.8009, 0.7664, 0.7442],
    [4.0, "strike-slip", 20.0, 760.0, 0.00442895, 0.00486184, 0.000501215, 0.8009, 0.7664, 0.7442],
    [4.0, "strike-slip", 100.0, 200.0, 0.000736103, 0.00153829, 0.000261413, 0.741, 0.7228, 0.7294],
    [4.0, "strike-slip", 0.0, 400.0, 0.0464417, 0.04586, 0.00487304, 0.8009, 0.7664, 0.7442],
    [5.0, "strike-slip", 20.0, 760.0, 0.0298779, 0.0360803, 0.00584835, 0.7022, 0.6852, 0.7109],
    [5.0, "strike-slip", 100.0, 760.0, 0.00302542, 0.00459583, 0.000883251, 0.7022, 0.6852, 0.7109],
    [5.0, "strike-slip", 20.0, 400.0, 0.0427334, 0.0595058, 0.0112619, 0.7022, 0.6852, 0.7109],
    [5.0, "strike-slip", 0.0, 760.0, 0.16414, 0.17093, 0.0259146, 0.7022, 0.6852, 0.7109],
    [5.0, "strike-slip", 100.0, 400.0, 0.00443291, 0.00785239, 0.00172922, 0.7022, 0.6852, 0.7109],
    [5.0, "strike-slip", 0.0, 400.0, 0.21802, 0.252969, 0.0474338, 0.7022, 0.6852, 0.7109],
    [5.0, "strike-slip", 0.0, 200.0, 0.235873, 0.286494, 0.0712145, 0.644, 0.6405, 0.6944],
    [5.0, "strike-slip", 100.0, 200.0, 0.00664987, 0.0138755, 0.00354526, 0.644, 0.6405, 0.6944],
    [5.0, "strike-slip", 20.0, 200.0, 0.0591524, 0.094249, 0.0213855, 0.644, 0.6405, 0.6944],
    [6.0, "strike-slip", 100.0, 400.0, 0.0197203, 0.0479486, 0.0161022, 0.6051, 0.6059, 0.6924],
    [6.0, "strike-slip", 0.0, 760.0, 0.407003, 0.707934, 0.181281, 0.6051, 0.6059, 0.6924],
    [6.0, "strike-slip", 0.0, 400.0, 0.505082, 0.948491, 0.316706, 0.6051, 0.6059, 0.6924],
    [6.0, "strike-slip", 100.0, 200.0, 0.0285964, 0.0809054, 0.0319628, 0.5493, 0.56, 0.6744],
    [6.0, "strike-slip", 20.0, 760.0, 0.0990773, 0.18096, 0.046683, 0.6051, 0.6059, 0.6924],
    [6.0, "strike-slip", 100.0, 760.0, 0.0135966, 0.0284846, 0.00828232, 0.6051, 0.6059, 0.6924],
    [6.0, "strike-slip", 0.0, 200.0, 0.435753, 0.789377, 0.383199, 0.5493, 0.56, 0.6744],
    [6.0, "strike-slip", 20.0, 400.0, 0.135537, 0.279621, 0.0871926, 0.6051, 0.6059, 0.6924],
    [6.0, "strike-slip", 20.0, 200.0, 0.16176, 0.361948, 0.143749, 0.5493, 0.56, 0.6744],
    [7.0, "strike-slip", 0.0, 200.0, 0.4708, 0.973361, 0.691284, 0.5493, 0.56, 0.6744],
    [7.0, "strike-slip", 20.0, 200.0, 0.220664, 0.499095, 0.281115, 0.5493, 0.56, 0.6744],
    [7.0, "strike-slip", 0.0, 760.0, 0.459874, 0.928775, 0.340345, 0.6051, 0.6059, 0.6924],
    [7.0, "strike-slip", 20.0, 760.0, 0.149712, 0.2875, 0.10001, 0.6051, 0.6059, 0.6924],
    [7.0, "strike-slip", 0.0, 400.0, 0.564823, 1.22568, 0.590399, 0.6051, 0.6059, 0.6924],
    [7.0, "strike-slip", 100.0, 760.0, 0.0278443, 0.0559242, 0.0208439, 0.6051, 0.6059, 0.6924],
    [7.0, "strike-slip", 100.0, 200.0, 0.0555204, 0.147534, 0.0767049, 0.5493, 0.56, 0.6744],
    [7.0, "strike-slip", 100.0, 400.0, 0.0398904, 0.0924561, 0.0401833, 0.6051, 0.6059, 0.6924],
    [7.0, "strike-slip", 20.0, 400.0, 0.200024, 0.429149, 0.183794, 0.6051, 0.6059, 0.6924],
    [8.0, "strike-slip", 20.0, 400.0, 0.293945, 0.562946, 0.294336, 0.6051, 0.6059, 0.6924],
    [8.0, "strike-slip", 0.0, 200.0, 0.508164, 1.03082, 0.949355, 0.5493, 0.56, 0.6744],
    [8.0, "strike-slip", 0.0, 400.0, 0.631486, 1.36173, 0.838473, 0.6051, 0.6059, 0.6924],
    [8.0, "strike-slip", 100.0, 200.0, 0.103624, 0.219073, 0.135409, 0.5493, 0.56, 0.6744],
    [8.0, "strike-slip", 20.0, 760.0, 0.226225, 0.392833, 0.16325, 0.6051, 0.6059, 0.6924],
    [8.0, "strike-slip", 100.0, 400.0, 0.0799588, 0.151292, 0.0759304, 0.6051, 0.6059, 0.6924],
    [8.0, "strike-slip", 0.0, 760.0, 0.519613, 1.04795, 0.486866, 0.6051, 0.6059, 0.6924],
    [8.0, "strike-slip", 20.0, 200.0, 0.295543, 0.577028, 0.412081, 0.5493, 0.56, 0.6744],
    [8.0, "strike-slip", 100.0, 760.0, 0.0570218, 0.0944287, 0.0399696, 0.6051, 0.6059, 0.6924],
    [5.0, "reverse", 50.0, 600.0, 0.0103357, 0.0153121, 0.00261141, 0.7022, 0.6852, 0.7109],
    [5.0, "reverse", 0.0, 600.0, 0.18, 0.208084, 0.0325842, 0.7022, 0.6852, 0.7109],
    [5.0, "reverse", 0.0, 250.0, 0.229549, 0.293174, 0.0642576, 0.6651, 0.6568, 0.7004],
    [5.0, "reverse", 50.0, 250.0, 0.0170369, 0.0308726, 0.00640721, 0.6651, 0.6568, 0.7004],
    [7.0, "reverse", 50.0, 600.0, 0.0724549, 0.153111, 0.0532963, 0.6051, 0.6059, 0.6924],
    [7.0, "reverse", 50.0, 250.0, 0.105909, 0.261013, 0.11805, 0.5694, 0.5767, 0.681],
    [7.0, "reverse", 0.0, 600.0, 0.497295, 1.10661, 0.424675, 0.6051, 0.6059, 0.6924],
    [7.0, "reverse", 0.0, 250.0, 0.50854, 1.1454, 0.693973, 0.5694, 0.5767, 0.681],
    [6.0, "unspecified", 20.0, 250.0, 0.150448, 0.336672, 0.122286, 0.5694, 0.5767, 0.681],
    [6.0, "unspecified", 20.0, 760.0, 0.0953544, 0.177661, 0.0453668, 0.6051, 0.6059, 0.6924],
    [6.0, "unspecified", 75.0, 250.0, 0.0375189, 0.0972688, 0.034193, 0.5694, 0.5767, 0.681],
    [6.0, "unspecified", 75.0, 760.0, 0.0204155, 0.041428, 0.0111715, 0.6051, 0.6059, 0.6924],
    [8.0, "unspecified", 75.0, 760.0, 0.0767005, 0.127187, 0.0508387, 0.6051, 0.6059, 0.6924],
    [8.0, "unspecified", 75.0, 250.0, 0.124906, 0.251978, 0.140695, 0.5694, 0.5767, 0.681],
    [8.0, "unspecified", 20.0, 760.0, 0.217725, 0.385671, 0.158647, 0.6051, 0.6059, 0.6924],
    [8.0, "unspecified", 20.0, 250.0, 0.294691, 0.589235, 0.376351, 0.5694, 0.5767, 0.681]
]
POINTS_OK = ["34.40, -118.80", "34.4,-118.8", "34.4 -118.8", " 34.40 ;  -118.80 ", "34.40\u00b0, \u2212118.80\u00b0", "34.40 N, 118.80 W", "34.4N 118.8W", "118.80 W, 34.40 N",
             "-118.8 E, 34.4 N", "34.40n 118.80w", "+34.4, -118.8"]
POINTS_BAD = ["", "34.40", "north of Los Angeles", "34.40, -118.80, 12", "34.40, abc"]
SCENARIOS = [
    {"magnitude": 7.0, "mechanism": "strike-slip", "trace": [[34.40, -118.80], [34.10, -118.30]], "spacing": 0.05},
    {"magnitude": 6.7, "mechanism": "reverse", "trace": [[34.30, -118.65], [34.33, -118.45]], "dip": 40, "width_km": 20, "vs30": 360, "epsilon": 1, "spacing": 0.05, "margin_km": 60},
    {"magnitude": 6.5, "mechanism": "normal", "trace": [[36.5, -117.9], [36.9, -118.0]], "dip": 60, "width_km": 15, "vs30": 270, "epsilon": -0.5, "spacing": 0.04, "margin_km": 80},
]


class TestGroundMotionModel(unittest.TestCase):
    def test_against_reference_values(self):
        self.assertGreater(len(REFERENCE), 50)
        for M, mech, R, V, *vals in REFERENCE:
            for m, median, sd in zip(("PGA", "SA0P3", "SA1P0"), vals[:3], vals[3:]):
                self.assertAlmostEqual(bssa14.ground_motion(m, M, mech, R, V) / median, 1.0, delta=2e-5, msg=f"{m} M{M} {mech} R{R} V{V}")
                self.assertAlmostEqual(bssa14.sigma(m, M, R, V), sd, delta=1e-4, msg=f"sigma {m} M{M} R{R} V{V}")

    def test_behaves_as_a_ground_motion_model_should(self):
        gm = lambda **k: bssa14.ground_motion("SA1P0", k.get("M", 7.0), "strike-slip", k.get("R", 20.0), k.get("V", 400.0), k.get("eps", 0.0))
        self.assertGreater(gm(M=7.5), gm(M=6.5))                        # larger earthquakes shake harder
        self.assertGreater(gm(R=5), gm(R=50))                           # shaking falls off with distance
        self.assertGreater(gm(V=250), gm(V=760))                        # soft ground amplifies long periods
        self.assertAlmostEqual(gm(eps=1) / gm(), math.exp(bssa14.sigma("SA1P0", 7.0, 20.0, 400.0)))
        self.assertAlmostEqual(bssa14.ln_median("PGA", 6.0, "reverse", 10.0, 760.0),      # on reference rock the site term vanishes
                               bssa14._event(bssa14.COEFFS["PGA"], 6.0, "reverse") + bssa14._path(bssa14.COEFFS["PGA"], 6.0, 10.0))

    def test_inputs_outside_the_model_are_refused(self):
        for bad in ((9.0, "strike-slip", 760), (7.5, "normal", 760), (2.5, "reverse", 760), (7.0, "strike-slip", 100), (7.0, "strike-slip", 2000), (7.0, "oblique", 760)):
            with self.assertRaises(ValueError):
                bssa14.check_inputs(*bad)
        bssa14.check_inputs(8.5, "strike-slip", 150)


class TestScenario(unittest.TestCase):
    def test_distance_to_a_vertical_fault(self):
        g = scenario.geometry(scenario.check_params({"magnitude": 7, "mechanism": "strike-slip", "trace": [[34.0, -118.0], [34.5, -118.0]]}))
        self.assertAlmostEqual(g["length"], 55.6, delta=0.05)
        self.assertEqual(scenario.rjb(g, 34.2, -118.0), 0.0)
        self.assertAlmostEqual(scenario.rjb(g, 34.25, -118.0 + 10 / g["kx"]), 10.0, places=9)
        self.assertAlmostEqual(scenario.rjb(g, 34.25, -118.0 - 10 / g["kx"]), 10.0, places=9)
        self.assertAlmostEqual(scenario.rjb(g, 34.5 + 10 / g["ky"], -118.0), 10.0, places=9)
        self.assertAlmostEqual(scenario.rjb(g, 34.5 + 3 / g["ky"], -118.0 + 4 / g["kx"]), 5.0, places=9)

    def test_distance_to_a_dipping_fault(self):
        g = scenario.geometry(scenario.check_params({"magnitude": 7, "mechanism": "reverse", "trace": [[34.0, -118.0], [34.5, -118.0]], "dip": 45, "width_km": 20}))
        self.assertAlmostEqual(g["w"], 20 * math.cos(math.radians(45)))
        self.assertEqual(scenario.rjb(g, 34.25, -118.0 + 5 / g["kx"]), 0.0)                          # above the rupture
        self.assertAlmostEqual(scenario.rjb(g, 34.25, -118.0 - 5 / g["kx"]), 5.0, places=9)          # on the other side
        self.assertAlmostEqual(scenario.rjb(g, 34.25, -118.0 + 20 / g["kx"]), 20 - g["w"], places=9)
        flipped = scenario.geometry(scenario.check_params({"magnitude": 7, "mechanism": "reverse", "trace": [[34.5, -118.0], [34.0, -118.0]], "dip": 45, "width_km": 20}))
        self.assertEqual(scenario.rjb(flipped, 34.25, -118.0 - 5 / flipped["kx"]), 0.0)              # swapping the ends flips the dip direction

    def test_layer_holds_the_model_values_at_its_nodes(self):
        p = scenario.check_params(SCENARIOS[1])
        layer = scenario.build(p, META)
        self.assertEqual(grid.check(layer), ([], []))
        self.assertEqual((layer["kind"], layer["levels"][0]["label"]), ("scenario", "Median plus 1 standard deviation"))
        g, gr = scenario.geometry(p), layer["grid"]
        self.assertAlmostEqual(gr["lat_min"] / gr["dlat"], round(gr["lat_min"] / gr["dlat"]), places=6)      # snapped to the spacing
        for j, i in ((0, 0), (5, 9), (gr["nlat"] - 1, gr["nlon"] - 1), (gr["nlat"] // 2, gr["nlon"] // 2)):
            r = scenario.rjb(g, gr["lat_min"] + j * gr["dlat"], gr["lon_min"] + i * gr["dlon"])
            for m in ("SA1P0", "SA0P3", "PGA"):
                self.assertAlmostEqual(layer["levels"][0]["measures"][m][j * gr["nlon"] + i] * grid.SCALE, bssa14.ground_motion(m, 6.7, "reverse", r, 360.0, 1.0), delta=0.00051)
        s = layer["source"]["scenario"]
        self.assertEqual((s["magnitude"], s["mechanism"], s["vs30_m_per_s"], s["epsilon"], s["component"]), (6.7, "reverse", 360.0, 1.0, "RotD50"))
        self.assertIn("Boore", s["model"])
        peak = max(layer["levels"][0]["measures"]["PGA"]) * grid.SCALE
        self.assertTrue(0.3 < peak < 2.0, peak)

    def test_footprint_of_the_rupture_is_recorded(self):
        vertical = scenario.build(SCENARIOS[0], META)["source"]["scenario"]
        self.assertNotIn("surface_projection", vertical)                    # a vertical fault's footprint is its trace
        self.assertEqual(vertical["trace"], [[34.40, -118.80], [34.10, -118.30]])
        p = scenario.check_params({"magnitude": 7, "mechanism": "reverse", "trace": [[34.0, -118.0], [34.5, -118.0]], "dip": 45, "width_km": 20, "spacing": 0.1, "margin_km": 30})
        fp = scenario.build(p, META)["source"]["scenario"]["surface_projection"]
        g = scenario.geometry(p)
        self.assertEqual(fp[0], [34.0, -118.0]); self.assertEqual(fp[1], [34.5, -118.0])                       # the two ends of the trace
        self.assertAlmostEqual(fp[2][0], 34.5, places=5); self.assertAlmostEqual(fp[3][0], 34.0, places=5)      # carried due east, down dip
        self.assertAlmostEqual((fp[2][1] + 118.0) * g["kx"], g["w"], delta=0.002)                               # by the width times cos(dip)
        self.assertAlmostEqual(fp[2][1], fp[3][1], places=5)
        for lat, lon in fp:                                                                                     # every corner lies on the rupture: distance zero
            self.assertLess(scenario.rjb(g, lat, lon), 0.002)

    def test_points_as_people_write_them(self):
        for text in POINTS_OK:
            self.assertEqual(scenario.parse_point(text), [34.4, -118.8], text)
        self.assertEqual(scenario.parse_point("34.4, 118.8"), [34.4, 118.8])          # the sign is never changed silently
        self.assertEqual(scenario.parse_point("33.5 S, 70.6 W"), [-33.5, -70.6])
        for text in POINTS_BAD:
            with self.assertRaises(ValueError, msg=text):
                scenario.parse_point(text)

    def test_bad_descriptions_are_refused(self):
        ok = SCENARIOS[0]
        for change in ({"magnitude": 9.2}, {"trace": [[34.0, -118.0], [34.0, -118.0]]}, {"dip": 0}, {"dip": 120}, {"vs30": 90}, {"epsilon": 5}, {"spacing": 0.0001},
                       {"margin_km": 900}, {"mechanism": "sideways"}, {"trace": [[134.0, -118.0], [34.0, -118.0]]}, {"spacing": 0.005, "margin_km": 250}):
            with self.assertRaises(ValueError, msg=str(change)):
                scenario.build(dict(ok, **change), META)


class TestRelease(unittest.TestCase):
    """The version and the citation must agree everywhere they are written down."""
    def test_version_citation_and_license_agree(self):
        v = sr_hazardmaker.__version__
        for path, needle in (("pyproject.toml", f'version = "{v}"'), ("CITATION.cff", f"version: {v}"), ("README.md", f"(Version {v})"),
                             ("README.md", sr_hazardmaker.citation()["bibtex"]), ("CHANGELOG.md", f"## {v} "),
                             ("LICENSE", "Copyright (c) 2026, E. Taciroglu"), ("CITATION.cff", "license: BSD-3-Clause"), ("pyproject.toml", "BSD-3-Clause"),
                             ("CITATION.cff", f"doi: {sr_hazardmaker.__doi__}"), ("README.md", f"https://doi.org/{sr_hazardmaker.__doi__}"),
                             ("README.md", sr_hazardmaker.citation()["bibtex"])):
            self.assertIn(needle, read(os.path.join(ROOT, path)), f"{path} should contain {needle!r}")


class TestCommandLine(Base):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        old = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = out, err
        try:
            code = cli.main(list(argv))
        finally:
            sys.stdout, sys.stderr = old
        return code, out.getvalue(), err.getvalue()

    def test_a_layer_must_state_its_source(self):
        code, _, err = self.run_cli("table", self.tb, "--out", os.path.join(self.tmp, "o1"))
        self.assertEqual(code, 2)
        self.assertIn("must say where it comes from", err)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "o1")))

    def test_scenario_from_the_command_line(self):
        out = os.path.join(self.tmp, "o3")
        args = ["scenario", "--magnitude", "7", "--mechanism", "strike-slip", "--from", "34.40,-118.80", "--to", "34.10,-118.30", "--spacing", "0.05", "--out", out]
        code, _, err = self.run_cli(*args)
        self.assertEqual(code, 2)                                   # no maker named
        self.assertIn("publisher", err)
        code, text, _ = self.run_cli(*args, "--publisher", "A. Researcher", "--id", "my-scenario")
        self.assertEqual(code, 0, text)
        layer = grid.load(os.path.join(out, "my-scenario.json"))
        self.assertEqual((layer["title"], layer["source"]["publisher"]), ("M 7 strike-slip scenario earthquake", "A. Researcher"))
        self.assertIn("command line (scenario builder)", layer["provenance"]["generator"])
        self.assertEqual(self.run_cli("scenario", "--magnitude", "9.5", "--mechanism", "reverse", "--from", "34,-118", "--to", "35,-118", "--publisher", "x")[0], 2)

    def test_writes_layer_and_index_entry(self):
        out = os.path.join(self.tmp, "o2")
        code, text, _ = self.run_cli("nshm", self.zip, "--out", out, "--date", "2026-11-18")
        self.assertEqual(code, 0, text)
        path = os.path.join(out, "usgs-nshm-2023r2-vs760.json")
        entry = json.loads(read(os.path.join(out, "usgs-nshm-2023r2-vs760.index-entry.json")))
        raw = read(path, "rb")
        import hashlib
        self.assertEqual(entry["sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(entry["bytes"], len(raw))
        self.assertIn(entry["sha256"], text)
        layer = grid.load(path)
        self.assertEqual(layer["source"]["retrieved_utc"], "2026-11-18")
        self.assertEqual(self.run_cli("check", path)[0], 0)
        code, text, _ = self.run_cli("shakemap", self.sm_zip, "--out", out)
        self.assertEqual(code, 0, text)
        self.assertTrue(os.path.exists(os.path.join(out, "shakemap-testevent01-v3.json")))


@unittest.skipUnless(NODE, "Node.js is needed to compare with the browser converters")
class TestBrowserConvertersAgree(Base):
    def same(self, a, b):
        a, b = strip(a), strip(b)
        self.assertEqual(sorted(a), sorted(b))
        for key in a:
            self.assertEqual(a[key], b[key], key)

    def test_hazard_model(self):
        for files in (list(self.tables.values()), [self.zip], [self.zip64]):
            py, label, groups = self.nshm_layer(files)
            r = self.js("nshm", {"files": files, "meta": META})
            self.assertNotIn("error", r, r)
            self.same(py, r["layer"])
            self.assertEqual(r["defaults"], nshm.defaults(label, files + [it["name"] for it in groups[label].values()]))

    def test_shakemap(self):
        for path in (self.sm_new, self.sm_old, self.sm_shuffled, self.sm_zip):
            text, name = shakemap.read_text(path)
            p = shakemap.parse(text, name)
            r = self.js("shakemap", {"files": [path], "meta": META})
            self.assertNotIn("error", r, r)
            self.same(shakemap.build(p, META, name), r["layer"])
            self.assertEqual(r["defaults"], shakemap.defaults(p))
        self.assertIn("error", self.js("shakemap", {"files": [self.sm_badunit], "meta": META}))

    def test_table(self):
        t = table.read(read(self.tb))
        mapping = dict(t["guess"], PGA=2)
        r = self.js("table", {"files": [self.tb], "meta": META, "mapping": mapping, "unit": "cm/s²"})
        self.assertNotIn("error", r, r)
        self.assertEqual(r["guess"], t["guess"])
        self.same(table.build(t, mapping, "cm/s²", dict(META, file_name="simulation_output.csv")), r["layer"])
        self.assertIn("error", self.js("table", {"files": [self.tb_scattered], "meta": META, "unit": "g"}))

    def test_reading_values_and_checking(self):
        layer, _, _ = self.nshm_layer([self.zip])
        path = os.path.join(self.tmp, "layer.json")
        with open(path, "w") as f:
            f.write(grid.serialize(layer))
        rng = random.Random(11)
        pts = [[rng.uniform(31.5, 43.0), rng.uniform(-125.5, -113.0)] for _ in range(4000)]
        js = self.js("valueat", {"layer": path, "points": pts})
        py = [grid.value_at(layer, layer["levels"][0]["measures"]["SA1P0"], la, lo) for la, lo in pts]
        self.assertEqual(js, py)                      # identical, including where there is no value
        self.assertGreater(sum(v is None for v in py), 50)
        bad = json.loads(json.dumps(layer)); bad["levels"][0]["measures"].pop("SA1P0")
        far = json.loads(json.dumps(layer)); far["grid"]["lat_min"] = 5.0; far["source"] = {}
        paths = []
        for i, h in enumerate((layer, bad, far)):
            paths.append(os.path.join(self.tmp, f"c{i}.json")); dump(h, paths[-1])
        for h, r in zip((layer, bad, far), self.js("check", {"layers": paths})):
            errors, warnings = grid.check(h)
            self.assertEqual((r["errors"], r["warnings"]), (errors, warnings))
            if not errors:
                self.assertEqual(r["summary"], grid.summary(h))

    def test_every_layer_names_the_tool_and_its_maker(self):
        py = self.nshm_layer(list(self.tables.values()))[0]
        js = self.js("nshm", {"files": list(self.tables.values()), "meta": META})["layer"]
        for layer in (py, js):
            self.assertEqual(layer["provenance"]["made_with"], grid.MADE_WITH)
            self.assertIn("E. Taciroglu", layer["provenance"]["made_with"])
        self.assertIn("browser", js["provenance"]["generator"])

    def test_same_citation_and_version(self):
        r = self.js("cite", {})
        self.assertEqual(r["citation"], sr_hazardmaker.citation())
        self.assertEqual(r["version"], sr_hazardmaker.__version__)
        self.assertIn("Taciroglu, E.", r["citation"]["text"])
        self.assertRegex(sr_hazardmaker.__doi__, r"^10\.5281/zenodo\.\d+$")
        self.assertIn("https://doi.org/" + sr_hazardmaker.__doi__, r["citation"]["text"])
        self.assertIn("doi     = {" + sr_hazardmaker.__doi__ + "}", r["citation"]["bibtex"])
        self.assertIn(sr_hazardmaker.__doi__, grid.MADE_WITH)
        self.assertIn(sr_hazardmaker.__version__, r["citation"]["bibtex"])

    def test_ground_motion_model(self):
        cases = [r[:4] for r in REFERENCE] + [[7.3, "strike-slip", 3.7, 333.0], [5.1, "normal", 150.0, 1200.0], [4.6, "unspecified", 280.0, 226.0]]
        r = self.js("model", {"cases": cases})
        py = [[[bssa14.ln_median(m, *c), bssa14.sigma(m, c[0], c[2], c[3])] for m in ("PGA", "SA0P3", "SA1P0")] for c in cases]
        # The two languages use different math libraries, so the raw values can differ in the last digit or two.
        # Thirteen significant digits is far finer than the 0.001 g a layer stores; the layers themselves must be identical.
        for case, a, b in zip(cases, r, py):
            for (ja, js_), (pa, ps) in zip(a, b):
                self.assertAlmostEqual(ja, pa, delta=1e-13 * max(1.0, abs(pa)), msg=str(case))
                self.assertAlmostEqual(js_, ps, delta=1e-13, msg=str(case))

    def test_scenario_builder(self):
        r = self.js("scenario", {"cases": SCENARIOS, "meta": META})
        self.assertNotIn("error", r, r)
        for p, layer, d in zip(SCENARIOS, r["layers"], r["defaults"]):
            self.same(scenario.build(p, META), layer)
            self.assertEqual(scenario.defaults(p), d)
        for bad in ({"magnitude": 9.2}, {"vs30": 90}, {"trace": [[34.0, -118.0], [34.0, -118.0]]}):
            self.assertIn("error", self.js("scenario", {"cases": [dict(SCENARIOS[0], **bad)], "meta": META}))

    def test_points_as_people_write_them(self):
        texts = POINTS_OK + ["34.4, 118.8", "33.5 S, 70.6 W"] + POINTS_BAD
        expected = []
        for text in texts:
            try:
                expected.append(scenario.parse_point(text))
            except ValueError:
                expected.append(None)
        self.assertEqual(self.js("points", {"texts": texts}), expected)

    def test_identifier_from_title(self):
        strings = ["USGS National Seismic Hazard Model 2023, revision 2, Vs30 760 m/s", "M 7.1 Ridgecrest — 2019", "  Çalışma: İstanbul senaryosu  ", "a" * 80, "---"]
        self.assertEqual(self.js("slug", {"strings": strings}), [cli.slug(s) for s in strings])


if __name__ == "__main__":
    unittest.main()
