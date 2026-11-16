/* Compare the page functions with eval/model.py on the certificate set.
   Prints only case ids and numbers. Does not print the appraiser field. */
const fs = require('fs');
const vm = require('vm');
const path = require('path');

const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
const match = html.match(/<script>([\s\S]*?)<\/script>/g) || [];
const code = match.map(block => block.replace(/^<script>/, '').replace(/<\/script>$/, '')).sort((a, b) => b.length - a.length)[0];
if (!code) throw new Error('script missing');
const cutAt = code.lastIndexOf("['objectType','district','dataScope','rooms','area','year','floor','totalFloors'].forEach");
if (cutAt < 0) throw new Error('cut point missing');
const src = code.slice(0, cutAt).replace('let GEO=null', 'var GEO=null');

function element() {
  return {
    style: {},
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    addEventListener() {},
    removeEventListener() {},
    setAttribute() {},
    getAttribute() { return ''; },
    appendChild() {},
    value: '',
    textContent: '',
    innerHTML: '',
    checked: false,
    options: [],
  };
}
const document = {
  getElementById() { return element(); },
  querySelector() { return null; },
  querySelectorAll() { return []; },
  addEventListener() {},
  createElement() { return element(); },
};
const context = {
  document,
  window: {},
  sessionStorage: { getItem() { return null; }, setItem() {}, removeItem() {} },
  localStorage: { getItem() { return null; }, setItem() {} },
  fetch: async () => { throw new Error('fetch disabled'); },
  console,
  setTimeout,
  clearTimeout,
  URL,
  Blob,
  Math, Date, Object, Array, JSON, String, Number,
  parseInt, parseFloat, isNaN, isFinite, RegExp, Map, Set, Promise,
  Intl, encodeURIComponent, decodeURIComponent,
  navigator: { serviceWorker: { register() { return Promise.resolve(); } } },
  location: { reload() {}, href: '' },
};
context.addEventListener = function() {};
context.removeEventListener = function() {};
context.window = context;
context.document = document;
vm.createContext(context);
vm.runInContext(src, context, { filename: 'index.html' });
if (typeof context.computeValuationForEval !== 'function') {
  throw new Error('computeValuationForEval missing');
}

const geoS = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'geo', 'streets.json'), 'utf8'));
const geoB = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'geo', 'buildings.json'), 'utf8'));
context.GEO = { streets: geoS.streets || {}, buildings: geoB.buildings || {} };

const raw = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'calc_data.json'), 'utf8'));
const records = raw.records
  .filter(r => context.validCity(r))
  .map(r => context.cityZoneRecord(r));
records.forEach(r => { delete r['вик']; delete r['виконавець']; });

function parseCases(file) {
  const text = fs.readFileSync(file, 'utf8').replace(/^\uFEFF/, '');
  const lines = text.split(/\n/).filter(Boolean);
  const head = lines[0].split(',');
  return lines.slice(1).map(line => {
    const cells = [];
    let cur = '', q = false;
    for (const ch of line) {
      if (ch === '"') { q = !q; continue; }
      if (ch === ',' && !q) { cells.push(cur); cur = ''; continue; }
      cur += ch;
    }
    cells.push(cur);
    const row = {};
    head.forEach((h, i) => { row[h] = cells[i]; });
    return row;
  });
}
const cases = parseCases(path.join(__dirname, 'data', 'cases.csv'));
const out = [];
for (const c of cases) {
  const t = {
    objectType: 'apartment',
    district: 'all',
    rooms: c.rooms_num ? parseInt(c.rooms_num, 10) : null,
    area: parseFloat(c.area_m2),
    street: c.street || null,
    house: c.house || null,
    year: c.year_num ? parseInt(c.year_num, 10) : null,
    floor: c.floor_num ? parseInt(c.floor_num, 10) : null,
    totalFloors: c.floors_num ? parseInt(c.floors_num, 10) : null,
    dataScope: '1',
  };
  const res = context.computeValuationForEval(records, t, c.date);
  out.push({
    case_id: c.case_id,
    ok: !!res.ok,
    fund: res.fund == null ? null : Math.round(res.fund * 100) / 100,
    total: res.total == null ? null : Math.round(res.total * 100) / 100,
    used: res.used || 0,
    scope: res.scope == null ? '' : String(res.scope),
    radius: res.radius == null ? null : Math.round(res.radius * 10) / 10,
    geo: res.geo || '',
    coef: res.coef == null ? null : res.coef,
  });
}
fs.writeFileSync('/tmp/js_preds.json', JSON.stringify(out));
console.log('js cases', out.length, 'ok', out.filter(x => x.ok).length);
