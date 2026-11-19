/* Runs the browser converters under Node, so the tests can compare them with the Python ones.
   Usage: node tests/js_run.cjs <command> <out.json> <args.json>     (args.json holds the files and the layer's details) */
const fs = require("fs"), path = require("path");
const H = require("../docs/sr-hazard.js"); globalThis.SRHazard = H;
const C = require("../docs/converters.js");
const S = require("../docs/scenario.js");
const [command, out, argsPath] = process.argv.slice(2);
const A = JSON.parse(fs.readFileSync(argsPath, "utf8"));
const asFile = p => new File([fs.readFileSync(p)], path.basename(p));
(async () => {
  let result;
  if (command === "nshm"){
    const found = await C.nshmFind(A.files.map(asFile));
    const label = A.group || Object.keys(found.groups).find(g => found.groups[g].SA1P0);
    const items = found.groups[label];
    result = {defaults: C.nshmDefaults(label, A.files.concat(Object.values(items).map(i => i.name))), notes: found.notes, groups: Object.keys(found.groups),
              layer: await C.nshmBuild(items, A.meta)};
  } else if (command === "shakemap"){
    const {text, name} = await C.shakemapText(asFile(A.files[0])), p = C.shakemapParse(text, name);
    result = {defaults: C.shakemapDefaults(p), rows: p.rows, off: p.off, layer: C.shakemapBuild(p, A.meta, name)};
  } else if (command === "table"){
    const t = C.tableRead(fs.readFileSync(A.files[0], "utf8"));
    result = {guess: t.guess, header: t.header, layer: C.tableBuild(t, A.mapping || t.guess, A.unit, Object.assign({}, A.meta, {file_name: path.basename(A.files[0])}))};
  } else if (command === "check"){
    result = A.layers.map(p => { const h = JSON.parse(fs.readFileSync(p, "utf8")); const c = H.check(h); return {errors: c.errors, warnings: c.warnings, summary: c.errors.length ? null : H.summary(h)}; });
  } else if (command === "valueat"){
    const h = JSON.parse(fs.readFileSync(A.layer, "utf8")), values = h.levels[0].measures.SA1P0;
    result = A.points.map(([lat, lon]) => H.valueAt(h, values, lat, lon));
  } else if (command === "model"){
    result = A.cases.map(([m, k, r, v]) => ["PGA", "SA0P3", "SA1P0"].map(x => [S.lnMedian(x, m, k, r, v), S.sigma(x, m, r, v)]));
  } else if (command === "scenario"){
    result = {layers: A.cases.map(c => S.build(c, A.meta)), defaults: A.cases.map(c => S.defaults(c))};
  } else if (command === "points"){
    result = A.texts.map(t => { try { return S.parsePoint(t); } catch (e){ return null; } });
  } else if (command === "cite"){
    result = {citation: H.citation(), version: H.VERSION};
  } else if (command === "slug"){
    result = A.strings.map(C.slug);
  } else throw new Error("unknown command " + command);
  fs.writeFileSync(out, JSON.stringify(result));
})().catch(err => { fs.writeFileSync(out, JSON.stringify({error: String(err && err.message || err)})); process.exitCode = 3; });
