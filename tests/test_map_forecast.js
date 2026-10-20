/*
 * Exercise the map's actual JavaScript, without a browser or npm dependencies.
 * Only Leaflet's drawing/event API and the three forecast DOM nodes are doubled.
 * Run: node --test tests/test_map_forecast.js
 */
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const root = path.resolve(__dirname, "..");
const forecast = JSON.parse(fs.readFileSync(path.join(root, "assets/forecast.json"), "utf8"));
const incidents = JSON.parse(fs.readFileSync(path.join(root, "assets/map_data.json"), "utf8"));

function productionFunction(source, name) {
  // Top-level functions in these pages use two-space indentation. Nested
  // functions use at least four, so this boundary also keeps their real bodies.
  const start = source.indexOf("  function " + name + "(");
  assert.notEqual(start, -1, "Missing production function " + name);
  const endOfLine = source.indexOf("\n", start);
  const firstLine = source.slice(start, endOfLine).trim();
  if (firstLine.endsWith("}")) return firstLine;
  const end = /^  }\r?$/m.exec(source.slice(endOfLine + 1));
  assert.ok(end, "Missing function boundary for " + name);
  return source.slice(start, endOfLine + 1 + end.index + end[0].length);
}

function productionObject(source, name) {
  const declaration = new RegExp("^  var " + name + " = \\{[\\s\\S]*?^  };", "m").exec(source);
  assert.ok(declaration, "Missing production object " + name);
  return declaration[0];
}

function eventTarget(object = {}) {
  const handlers = new Map();
  object.on = function (names, callback) {
    if (typeof names === "object") {
      Object.entries(names).forEach(([name, fn]) => this.on(name, fn));
    } else {
      names.split(/\s+/).forEach(name => {
        if (!handlers.has(name)) handlers.set(name, []);
        handlers.get(name).push(callback);
      });
    }
    return this;
  };
  object.addEventListener = object.on;
  object.fire = function (name, event = {}) {
    event.target = this;
    event.originalEvent ||= { stopPropagation() {}, preventDefault() {} };
    (handlers.get(name) || []).forEach(callback => callback.call(this, event));
    return this;
  };
  return object;
}

function harness(file) {
  const source = fs.readFileSync(path.join(root, file), "utf8");
  const nodes = {
    forecastInfo: eventTarget({ innerHTML: "", style: {} }),
    forecastBox: eventTarget({ innerHTML: "", style: { display: "none" } }),
    forecastToggle: eventTarget({ checked: true, style: {} }),
  };
  const layers = new Set();
  const map = eventTarget({
    hasLayer(layer) { return layers.has(layer); },
    removeLayer(layer) { layers.delete(layer); return this; },
    addLayer(layer) { layers.add(layer); return this; },
  });
  const circles = [], points = [], details = [];

  function layer(position, options) {
    const attributes = new Map();
    const element = {
      setAttribute(name, value) { attributes.set(name, value); },
      getAttribute(name) { return attributes.get(name); },
      querySelector() { return null; },
    };
    return eventTarget({
      position,
      options,
      addTo(target) {
        const alreadyAdded = target.hasLayer(this);
        target.addLayer(this);
        if (!alreadyAdded) this.fire("add");
        return this;
      },
      bindTooltip(html) { this.tooltip = html; return this; },
      setStyle(style) { Object.assign(this.options, style); return this; },
      getElement() { return element; },
    });
  }
  const context = vm.createContext({
    document: {
      getElementById(id) {
        assert.ok(nodes[id], "Unexpected DOM dependency: " + id);
        return nodes[id];
      },
    },
    map,
    L: {
      circle(position, options) {
        const value = layer(position, options);
        circles.push(value);
        return value;
      },
      marker(position, options) {
        const value = layer(position, options);
        points.push(value);
        return value;
      },
      divIcon(options) { return options; },
      DomEvent: { stop() {}, stopPropagation() {} },
    },
    forecastLayers: [],
    markers: [],
    selectedMarker: null,
    focusedCluster: null,
    GLOBAL_DATA: incidents,
    clusterOf() { return null; },
    sendToDetails(incident, category, label) { details.push({ incident, category, label }); },
  });
  const functions = [
    "escapeHtml", "hashJitter", "daysText", "daysShort", "measured", "marksText",
    "shortMarks", "germanDate", "timingInfoHtml", "timeStatementsHtml",
    "forecastDetailsHtml", "buildForecastLayer", "buildMarkers",
  ];
  vm.runInContext(
    [productionObject(source, "CATS"), productionObject(source, "RAW_LABELS")]
      .concat(functions.map(name => productionFunction(source, name))).join("\n"),
    context,
    { filename: file },
  );
  return { context, nodes, map, layers, circles, points, details };
}

function districtCircle(h, district) {
  const result = h.circles.find(circle =>
    circle.position[0] === district.lat && circle.position[1] === district.lon);
  assert.ok(result, "No forecast circle for " + district.stadtteil);
  return result;
}

for (const file of ["assets/map_local.html", "docs/map_local.html"]) {
  test(file + ": every district has a forecast halo; only the top group is red", () => {
    const h = harness(file);
    h.context.buildForecastLayer(incidents, forecast);
    assert.equal(h.circles.length, forecast.districts.length);
    assert.equal(h.circles.length, 23);
    assert.equal(h.context.forecastLayers.length, 23);
    assert.equal(h.layers.size, 23);
    const red = h.circles.filter(circle => circle.options.fillColor === "#dc2626");
    assert.equal(red.length, forecast.k);
    assert.equal(h.circles.length - red.length, 18);
    for (const district of forecast.districts) {
      const circle = districtCircle(h, district);
      assert.equal(circle.options.fillColor === "#dc2626", district.elevated,
        district.stadtteil + " must preserve the forecast's actual risk group");
      assert.equal(circle.options.pane, "forecastPane");
      assert.equal(circle.options.interactive, true);
    }
  });

  for (const name of ["Schwachhausen", "Vahr"]) {
    test(file + ": " + name + " halo shows district timing rather than an individual offense", () => {
      const h = harness(file);
      const input = structuredClone(forecast);
      const district = input.districts.find(item => item.stadtteil === name);
      // Keep this regression exercised if a later daily update ranks either
      // district in the top five; retain its actual coordinates/timing data.
      district.elevated = false;
      district.rank = Math.max(district.rank, input.k + 1);
      h.context.buildForecastLayer(incidents, input);
      const circle = districtCircle(h, district);
      circle.fire("mouseover");
      const box = h.nodes.forecastBox;
      assert.equal(box.style.display, "block");
      assert.match(box.innerHTML, new RegExp("Prognose: " + name));
      assert.match(box.innerHTML, /Nächste Meldung hier/);
      assert.match(box.innerHTML, new RegExp("binnen " + district.timing.any.t50 + " Tagen"));
      assert.doesNotMatch(box.innerHTML, /erh(?:ö|&ouml;)htes Risiko/);
      const localIncident = incidents.find(item => item.stadtteil === name);
      assert.ok(!box.innerHTML.includes(localIncident.title), "Forecast must not substitute a release title");
      circle.fire("click");
      assert.equal(h.details.length, 0, "A forecast halo must not select a single incident");
    });
  }

  test(file + ": an elevated district keeps its risk label and timing", () => {
    const h = harness(file);
    h.context.buildForecastLayer(incidents, forecast);
    const district = forecast.districts.find(item => item.elevated);
    districtCircle(h, district).fire("mouseover");
    assert.match(h.nodes.forecastBox.innerHTML, /erh(?:ö|&ouml;)htes Risiko/);
    assert.match(h.nodes.forecastBox.innerHTML, /Nächste Meldung hier/);
  });

  test(file + ": hover closes on exit; clicking pins until map click or toggle off", () => {
    const h = harness(file);
    h.context.buildForecastLayer(incidents, forecast);
    const circle = districtCircle(h, forecast.districts.find(item => item.stadtteil === "Vahr"));
    circle.fire("mouseover").fire("mouseout");
    assert.equal(h.nodes.forecastBox.style.display, "none");
    circle.fire("click").fire("mouseout");
    assert.equal(h.nodes.forecastBox.style.display, "block", "Click must pin the forecast for reading");
    h.map.fire("click");
    assert.equal(h.nodes.forecastBox.style.display, "none");
    circle.fire("click");
    h.nodes.forecastToggle.checked = false;
    h.nodes.forecastToggle.fire("change");
    assert.equal(h.layers.size, 0);
    assert.equal(h.nodes.forecastBox.style.display, "none");
    h.nodes.forecastToggle.checked = true;
    h.nodes.forecastToggle.fire("change");
    assert.equal(h.layers.size, forecast.districts.length);
    circle.fire("mouseover").fire("mouseout");
    assert.equal(h.nodes.forecastBox.style.display, "none", "Toggle off must clear pinned state too");
  });

  test(file + ": forecast halos expose district names and support keyboard open/close", () => {
    const h = harness(file);
    h.context.buildForecastLayer(incidents, forecast);
    const circle = districtCircle(h, forecast.districts.find(item => item.stadtteil === "Schwachhausen"));
    const element = circle.getElement();
    assert.equal(element.getAttribute("aria-label"), "Prognose: Schwachhausen");
    assert.equal(element.getAttribute("role"), "button");
    assert.equal(element.getAttribute("tabindex"), "0");
    for (const key of ["Enter", " "]) {
      element.onkeydown({ key });
      assert.equal(h.nodes.forecastBox.style.display, "block");
      assert.match(h.nodes.forecastBox.innerHTML, /Prognose: Schwachhausen/);
      circle.fire("mouseout");
      assert.equal(h.nodes.forecastBox.style.display, "block");
      element.onkeydown({ key: "Escape" });
      assert.equal(h.nodes.forecastBox.style.display, "none");
    }
    assert.equal(h.details.length, 0);
  });

  test(file + ": offenses keep clickable points without creating competing large halos", () => {
    const h = harness(file);
    h.context.buildMarkers(incidents);
    assert.equal(h.circles.length, 0, "Individual offenses must not recreate forecast-sized circles");
    assert.equal(h.points.length, incidents.length);
    assert.equal(h.context.markers.length, incidents.length);
    for (const precision of ["a", "d"]) {
      const index = incidents.findIndex(incident => incident.prec === precision);
      assert.notEqual(index, -1, "Missing incident precision fixture " + precision);
      h.points[index].fire("click");
      assert.equal(h.details.at(-1).incident.id, incidents[index].id);
      assert.ok(h.points[index].tooltip.includes(h.context.escapeHtml(incidents[index].title)));
    }
    assert.equal(h.details.length, 2);
  });
}
