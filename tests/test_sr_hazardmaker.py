"""Tests for SR-HazardMaker. Run from the repository root:

    python -m unittest discover -s tests -v

Every ground-motion value used here is made up inside this file. Nothing here is a hazard estimate.
The tests that compare the Python converters with the browser ones need Node.js 20 or later and
are skipped without it.
"""
import csv, io, json, math, os, random, shutil, subprocess, sys, tempfile, unittest, zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from sr_hazardmaker import cli, grid, nshm, shakemap, table   # noqa: E402

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

    def test_identifier_from_title(self):
        strings = ["USGS National Seismic Hazard Model 2023, revision 2, Vs30 760 m/s", "M 7.1 Ridgecrest — 2019", "  Çalışma: İstanbul senaryosu  ", "a" * 80, "---"]
        self.assertEqual(self.js("slug", {"strings": strings}), [cli.slug(s) for s in strings])


if __name__ == "__main__":
    unittest.main()
