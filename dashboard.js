/* Norway dashboard: renders data/dashboard.json (built by build_dashboard_data.py).
   Display definitions live here, keyed by the same indicator ids as the build script. */
(function () {
  'use strict';

  // ------------------------------------------------------------------ formatting helpers
  const fmt = {
    num(v, d) { return v == null ? '–' : v.toLocaleString('en-GB', { minimumFractionDigits: d, maximumFractionDigits: d }); },
    pct(v, d = 1) { return v == null ? '–' : fmt.num(v, d) + ' %'; },
    signedPct(v, d = 1) { return v == null ? '–' : (v > 0 ? '+' : '') + fmt.num(v, d) + ' %'; },
    pp(v, d = 1) { return v == null ? '–' : (v > 0 ? '+' : '') + fmt.num(v, d) + ' pp'; },
    bnFromMn(v, d = 1) { return v == null ? '–' : 'NOK ' + fmt.num(v / 1000, d) + ' bn'; },
    signedBnFromMn(v, d = 1) { return v == null ? '–' : (v > 0 ? '+' : v < 0 ? '−' : '') + 'NOK ' + fmt.num(Math.abs(v) / 1000, d) + ' bn'; },
    signedBn(v, d = 0) { return v == null ? '–' : (v > 0 ? '+' : v < 0 ? '−' : '') + 'NOK ' + fmt.num(Math.abs(v), d) + ' bn'; },
    trnFromBn(v, d = 2) { return v == null ? '–' : 'NOK ' + fmt.num(v / 1000, d) + ' trn'; },
    plain(v, d = 2) { return fmt.num(v, d); },
  };
  const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  function periodLabel(p, freq) {
    if (!p) return '';
    if (freq === 'D') { const [y, m, d] = p.split('-'); return `${+d} ${MONTHS[+m - 1]} ${y}`; }
    if (freq === 'M') { const [y, m] = p.split('-'); return `${MONTHS[+m - 1]} ${y}`; }
    if (freq === 'Q') { const [y, q] = p.split('-'); return `${q} ${y}`; }
    return p;
  }
  function periodTime(p) {   // fractional year, for range filtering
    const m = /^(\d{4})(?:-(?:Q(\d)|(\d{2})(?:-(\d{2}))?))?$/.exec(p);
    if (!m) return NaN;
    const y = +m[1];
    if (m[2]) return y + (m[2] - 1) / 4;
    if (m[3]) return y + (m[3] - 1) / 12 + (m[4] ? (m[4] - 1) / 365 : 0);
    return y;
  }
  const freqOf = (p) => (/^\d{4}-\d{2}-\d{2}$/.test(p) ? 'D' : /Q/.test(p) ? 'Q' : /^\d{4}-\d{2}$/.test(p) ? 'M' : 'A');
  const last = (s, k = 1) => { for (let i = s.values.length - 1; i >= 0; i--) { if (s.values[i] != null) { if (--k === 0) return { v: s.values[i], p: s.periods[i], i }; } } return { v: null, p: null, i: -1 }; };
  const cssVar = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  const SLOTS = ['--s1', '--s2', '--s3', '--s4', '--s5', '--s6', '--s7', '--s8'];

  // ------------------------------------------------------------------ indicator definitions
  // headline: the series carried by the tile; goodUp: true/false colours the change as good/bad, null keeps it neutral
  // views: series offered in the detail chart; "on" marks the default selection; kind: line | bar; short: pill/legend label
  const DEFS = [
    {
      id: 'gdp', title: 'GDP, Mainland Norway', kicker: 'Activity', goodUp: true,
      desc: 'Gross domestic product for Mainland Norway, i.e. excluding petroleum extraction, pipeline transport and ocean shipping. Volumes at constant 2023 prices, seasonally adjusted. The latest quarters are preliminary.',
      headline: { series: 'qoq', fmt: (v) => fmt.signedPct(v), sub: 'quarter-on-quarter, volume, s.a.' },
      secondary: [{ series: 'yoy', label: 'y/y', fmt: fmt.signedPct }],
      spark: 'qoq', sparkZero: true,
      views: [
        { s: 'qoq', short: 'q/q growth', kind: 'bar', on: true }, { s: 'yoy', short: 'y/y growth', kind: 'line', on: true }, { s: 'total_qoq', short: 'Total GDP, q/q', kind: 'line' },
        { s: 'level', short: 'Level, NOK mn', kind: 'line' }, { s: 'annual', short: 'Annual growth', kind: 'bar' }, { s: 'annual_total', short: 'Total GDP, annual', kind: 'bar' }],
      defaultYears: 10,
    },
    {
      id: 'inflation', title: 'Inflation', kicker: 'Prices', goodUp: null,
      desc: 'Twelve-month change in the consumer price index (CPI) and in CPI-ATE, the index adjusted for tax changes and excluding energy products that Norges Bank uses as its core measure. The inflation target is 2 per cent.',
      headline: { series: 'cpi_yoy', fmt: (v) => fmt.pct(v), sub: 'CPI, 12-month change' },
      secondary: [{ series: 'cpiate_yoy', label: 'CPI-ATE', fmt: fmt.pct }],
      spark: 'cpi_yoy', sparkZero: true, target: 2,
      views: [{ s: 'cpi_yoy', short: 'CPI', kind: 'line', on: true }, { s: 'cpiate_yoy', short: 'CPI-ATE', kind: 'line', on: true }, { s: 'cpiat_yoy', short: 'CPI-AT', kind: 'line' },
              { s: 'cpi_mom', short: 'CPI, m/m', kind: 'bar' }, { s: 'cpi_index', short: 'CPI index', kind: 'line' }],
      second: { title: 'Main CPI groups, 12-month change', series: ['grp_01', 'grp_04', 'grp_07', 'grp_11', 'grp_09', 'grp_03', 'grp_05', 'grp_08'] },
      defaultYears: 10,
    },
    {
      id: 'labour', title: 'Unemployment', kicker: 'Labour market', goodUp: false,
      desc: 'Unemployment rate from the Labour Force Survey (LFS), ages 15–74, seasonally adjusted three-month moving average dated by the middle month. Employment and participation rates are shares of the population.',
      headline: { series: 'unemployment', fmt: (v) => fmt.pct(v), sub: 'LFS unemployment rate, 3-month average' },
      secondary: [{ series: 'employment_rate', label: 'employment rate', fmt: fmt.pct }],
      spark: 'unemployment',
      views: [{ s: 'unemployment', short: 'Unemployment rate', kind: 'line', on: true }, { s: 'employment_rate', short: 'Employment rate', kind: 'line' }, { s: 'participation', short: 'Participation rate', kind: 'line' }],
      defaultYears: 10,
    },
    {
      id: 'rates', title: 'Interest rates', kicker: 'Monetary policy', goodUp: null,
      desc: 'Norges Bank’s policy rate (the sight deposit rate), the NOWA overnight money-market rate and generic government yields. Monthly averages give the long history; the daily series cover recent years and cannot be mixed with the monthly ones in the same chart.',
      headline: { series: 'policy_rate_daily', fmt: (v) => fmt.pct(v, 2), sub: 'policy rate' },
      secondary: [{ series: 'gov10y_daily', label: '10-year yield', fmt: (v) => fmt.pct(v, 2) }],
      spark: 'policy_rate',
      views: [{ s: 'policy_rate', short: 'Policy rate', kind: 'line', on: true }, { s: 'gov10y', short: '10-year yield', kind: 'line', on: true }, { s: 'gov3y', short: '3-year yield', kind: 'line' },
              { s: 'tbill3m', short: '3-month T-bill', kind: 'line' }, { s: 'nowa', short: 'NOWA', kind: 'line' },
              { s: 'policy_rate_daily', short: 'Policy rate, daily', kind: 'line' }, { s: 'gov10y_daily', short: '10-year yield, daily', kind: 'line' }],
      decisions: true, defaultYears: 10,
    },
    {
      id: 'fx', title: 'Exchange rates', kicker: 'The krone', goodUp: null,
      desc: 'The import-weighted krone index I-44 (higher means a weaker krone) and the krone against the euro and the US dollar. Monthly averages give the long history; the daily series cover recent years.',
      headline: { series: 'i44_daily', fmt: (v) => fmt.plain(v, 1), sub: 'I-44 index, 1995 = 100 (higher = weaker krone)' },
      secondary: [{ series: 'eurnok_daily', label: 'EUR', fmt: (v) => fmt.plain(v, 2) }, { series: 'usdnok_daily', label: 'USD', fmt: (v) => fmt.plain(v, 2) }],
      spark: 'i44',
      views: [{ s: 'i44', short: 'I-44', kind: 'line', on: true }, { s: 'eurnok', short: 'EUR/NOK', kind: 'line' }, { s: 'usdnok', short: 'USD/NOK', kind: 'line' }, { s: 'gbpnok', short: 'GBP/NOK', kind: 'line' }, { s: 'seknok', short: 'SEK/NOK (per 100)', kind: 'line' },
              { s: 'i44_daily', short: 'I-44, daily', kind: 'line' }, { s: 'eurnok_daily', short: 'EUR/NOK, daily', kind: 'line' }, { s: 'usdnok_daily', short: 'USD/NOK, daily', kind: 'line' }],
      defaultYears: 10,
    },
    {
      id: 'trade', title: 'Trade balance', kicker: 'External trade', goodUp: null,
      desc: 'Monthly trade in goods, seasonally adjusted. The total balance is dominated by oil and gas; the mainland balance compares exports excluding oil, gas, ships and platforms with imports excluding ships and platforms.',
      headline: { series: 'balance', fmt: (v) => fmt.bnFromMn(v), sub: 'goods, seasonally adjusted, per month' },
      secondary: [{ series: 'mainland_balance', label: 'mainland balance', fmt: fmt.bnFromMn }],
      spark: 'balance', sparkZero: true, scale: 1 / 1000, unitLabel: 'NOK bn',
      views: [{ s: 'balance', short: 'Trade balance', kind: 'bar', on: true }, { s: 'exports', short: 'Exports', kind: 'line' }, { s: 'imports', short: 'Imports', kind: 'line' }, { s: 'oilgas_exports', short: 'Oil & gas exports', kind: 'line' },
              { s: 'mainland_exports', short: 'Mainland exports', kind: 'line' }, { s: 'imports_ex_ships', short: 'Imports excl. ships', kind: 'line' }, { s: 'mainland_balance', short: 'Mainland balance', kind: 'line' }],
      defaultYears: 10,
    },
    {
      id: 'current_account', title: 'Current account', kicker: 'Balance of payments', goodUp: null,
      desc: 'Quarterly current account balance and its components, in current prices. The share of GDP uses four-quarter sums of both the balance and nominal GDP.',
      headline: { series: 'pct_gdp', fmt: (v) => fmt.pct(v), sub: 'per cent of GDP, last four quarters' },
      secondary: [{ series: 'balance', label: 'latest quarter', fmt: fmt.bnFromMn }],
      spark: 'pct_gdp', sparkZero: true,
      views: [{ s: 'pct_gdp', short: '% of GDP', kind: 'line', on: true }, { s: 'balance', short: 'Balance', kind: 'bar' }, { s: 'goods_services', short: 'Goods & services', kind: 'line' }, { s: 'goods', short: 'Goods', kind: 'line' },
              { s: 'services', short: 'Services', kind: 'line' }, { s: 'income', short: 'Income & transfers', kind: 'line' }, { s: 'balance_4q', short: 'Balance, 4-quarter sum', kind: 'line' }],
      scale: 1 / 1000, unitLabel: 'NOK bn', defaultYears: 20,
    },
    {
      id: 'oil_fund', title: 'The oil fund', kicker: 'Government Pension Fund Global', goodUp: true,
      desc: 'Market value of the Government Pension Fund Global at year-end, by asset class, as published by NBIM. The current year shows the latest value NBIM has published, not a year-end value.',
      headline: { series: 'value', fmt: (v) => fmt.trnFromBn(v), sub: 'market value', latestLabel: 'latest' },
      secondary: [{ series: 'pct_gdp', label: 'of GDP', fmt: (v) => fmt.pct(v, 0) }],
      spark: 'value', unitLabel: 'NOK bn',
      views: [{ s: 'value', short: 'Total', kind: 'line', on: true }, { s: 'equity', short: 'Equity', kind: 'line' }, { s: 'fixed_income', short: 'Fixed income', kind: 'line' }, { s: 'real_estate', short: 'Real estate', kind: 'line' },
              { s: 'renewables', short: 'Renewable infrastructure', kind: 'line' }, { s: 'pct_gdp', short: '% of GDP', kind: 'bar' }],
      defaultYears: 30,
    },
    {
      id: 'house_prices', title: 'House prices', kicker: 'Housing', goodUp: null,
      desc: 'Price index for existing dwellings, whole country, seasonally adjusted (2015 = 100), with changes on a year earlier for the country and the largest cities.',
      headline: { series: 'yoy', fmt: (v) => fmt.signedPct(v), sub: 'change on a year earlier' },
      secondary: [{ series: 'qoq', label: 'q/q s.a.', fmt: fmt.signedPct }],
      spark: 'yoy', sparkZero: true,
      views: [{ s: 'yoy', short: 'Norway, y/y', kind: 'line', on: true }, { s: 'yoy_001', short: 'Oslo, y/y', kind: 'line' }, { s: 'yoy_003', short: 'Bergen, y/y', kind: 'line' }, { s: 'yoy_004', short: 'Trondheim, y/y', kind: 'line' },
              { s: 'yoy_002', short: 'Stavanger, y/y', kind: 'line' }, { s: 'qoq', short: 'Norway, q/q s.a.', kind: 'bar' }, { s: 'index', short: 'Index, 2015=100', kind: 'line' }],
      defaultYears: 10,
    },
    {
      id: 'productivity', title: 'Productivity', kicker: 'Supply side', goodUp: true,
      desc: 'Labour productivity (value added per hour worked) and total factor productivity in Mainland Norway, from the annual national accounts. TFP is the Solow residual of a Cobb-Douglas production function with a capital share of one third.',
      headline: { series: 'labour_productivity_growth', fmt: (v) => fmt.signedPct(v), sub: 'labour productivity growth' },
      secondary: [{ series: 'tfp_growth', label: 'TFP growth', fmt: fmt.signedPct }],
      spark: 'labour_productivity_growth', sparkZero: true,
      views: [{ s: 'labour_productivity', short: 'Labour productivity', kind: 'line', on: true }, { s: 'tfp', short: 'TFP', kind: 'line', on: true }, { s: 'capital_per_hour', short: 'Capital per hour', kind: 'line' },
              { s: 'labour_productivity_growth', short: 'Labour productivity growth', kind: 'bar' }, { s: 'tfp_growth', short: 'TFP growth', kind: 'bar' },
              { s: 'output_growth', short: 'Value added growth', kind: 'line' }, { s: 'hours_growth', short: 'Hours growth', kind: 'line' }, { s: 'capital_growth', short: 'Capital growth', kind: 'line' }],
      defaultYears: 30, showMethod: true,
    },
  ];

  // ------------------------------------------------------------------ data load
  let DATA = null;
  const tilesEl = document.getElementById('tiles');
  fetch('data/dashboard.json', { cache: 'no-cache' })
    .then((r) => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
    .then((d) => { DATA = d; renderMeta(); renderTiles(); openFromHash(); })
    .catch((e) => { tilesEl.innerHTML = `<p class="d-error">Could not load the data file (${e.message}). If you opened this page from disk, serve it over http; the data is loaded with fetch().</p>`; });

  function renderMeta() {
    const el = document.getElementById('dash-meta');
    const gen = new Date(DATA.generated);
    const parts = [`Data last fetched ${gen.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })}`, 'updated automatically every morning'];
    const errs = Object.keys(DATA.errors || {});
    if (errs.length) parts.push(`<span class="warn">last fetch failed for: ${errs.join(', ')} (showing older data)</span>`);
    el.innerHTML = parts.join(' · ');
  }

  // ------------------------------------------------------------------ tiles
  function renderTiles() {
    tilesEl.innerHTML = '';
    for (const def of DEFS) {
      const ind = DATA.indicators[def.id];
      if (!ind) continue;
      const s = ind.series[def.headline.series];
      const L = last(s), P = last(s, 2);
      const b = document.createElement('button');
      b.className = 'tile' + (ind.stale ? ' stale' : '');
      b.type = 'button';
      b.setAttribute('aria-label', `${def.title}: ${def.headline.fmt(L.v)} (${periodLabel(L.p, s.freq)}). Open details.`);
      b.addEventListener('click', () => openDetail(def.id));
      const sec = (def.secondary || []).map((x) => { const ss = ind.series[x.series]; if (!ss) return ''; const l = last(ss); return `${x.label} ${x.fmt(l.v)}`; }).filter(Boolean).join(' · ');
      const plabel = (def.headline.latestLabel && isCurrentYear(L.p, s.freq)) ? `${L.p} (${def.headline.latestLabel})` : periodLabel(L.p, s.freq);
      b.innerHTML = `
        <div class="t-head"><div class="t-title">${def.title}</div><div class="t-period">${plabel}</div></div>
        <div class="t-value">${def.headline.fmt(L.v)}</div>
        <div class="t-sub">${def.headline.sub}</div>
        ${deltaHtml(def, s, L, P)}
        <div class="t-sub">${sec}</div>
        <div class="t-spark">${sparkline(ind.series[def.spark], def)}</div>
        <div class="t-foot"><span>${def.kicker}</span><span class="more">Explore →</span></div>`;
      tilesEl.appendChild(b);
    }
  }
  function isCurrentYear(p, freq) { return freq === 'A' && +p === new Date().getFullYear(); }

  function deltaHtml(def, s, L, P) {
    if (L.v == null || P.v == null) return '';
    const d = L.v - P.v;
    let txt;
    if (/per cent/.test(s.unit)) txt = fmt.pp(d, s.decimals);
    else if (s.unit.startsWith('NOK million')) txt = fmt.signedBnFromMn(d);
    else if (s.unit.startsWith('NOK billion')) txt = fmt.signedBn(d);
    else txt = (d > 0 ? '+' : '') + fmt.num(d, s.decimals);
    const arrow = d > 0 ? '▲' : d < 0 ? '▼' : '■';
    // colour only when a direction is unambiguously good or bad for the economy; otherwise stay neutral
    let cls = 'flat';
    if (d !== 0 && def.goodUp != null) cls = (d > 0) === def.goodUp ? 'up' : 'down';
    return `<div class="t-delta ${cls}">${arrow} ${txt} <span style="font-weight:400;color:var(--muted)">vs ${periodLabel(P.p, s.freq)}</span></div>`;
  }

  function sparkline(s, def) {
    if (!s) return '';
    const n = Math.min(s.values.length, s.freq === 'D' ? 500 : s.freq === 'M' ? 120 : s.freq === 'Q' ? 40 : 25);
    const vals = s.values.slice(-n), W = 240, H = 46, pad = 2;
    const nums = vals.filter((v) => v != null);
    if (nums.length < 2) return '';
    let min = Math.min(...nums), max = Math.max(...nums);
    if (def.sparkZero) { min = Math.min(min, 0); max = Math.max(max, 0); }
    if (max === min) { max += 1; min -= 1; }
    const x = (i) => pad + (i / (vals.length - 1)) * (W - 2 * pad);
    const y = (v) => pad + (1 - (v - min) / (max - min)) * (H - 2 * pad);
    let d = '', pen = false;
    vals.forEach((v, i) => { if (v == null) { pen = false; return; } d += (pen ? 'L' : 'M') + x(i).toFixed(1) + ' ' + y(v).toFixed(1); pen = true; });
    const zero = def.sparkZero && min < 0 && max > 0 ? `<line class="zero" x1="0" x2="${W}" y1="${y(0).toFixed(1)}" y2="${y(0).toFixed(1)}"/>` : '';
    const li = vals.length - 1 - [...vals].reverse().findIndex((v) => v != null);
    return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true">${zero}<path class="line" d="${d}"/><circle class="dot" cx="${x(li).toFixed(1)}" cy="${y(vals[li]).toFixed(1)}" r="2.5"/></svg>`;
  }

  // ------------------------------------------------------------------ detail dialog
  const dlg = document.getElementById('detail');
  const inner = document.getElementById('d-inner');
  let chart = null, chart2 = null, state = null;

  dlg.addEventListener('close', () => { destroyCharts(); if (location.hash) history.replaceState(null, '', location.pathname + location.search); });
  dlg.addEventListener('click', (e) => { if (e.target === dlg) dlg.close(); });
  window.addEventListener('hashchange', openFromHash);
  function openFromHash() { const id = location.hash.replace('#', ''); if (id && DEFS.some((d) => d.id === id) && DATA) openDetail(id, true); }
  function destroyCharts() { if (chart) { chart.destroy(); chart = null; } if (chart2) { chart2.destroy(); chart2 = null; } }

  function openDetail(id, fromHash) {
    const def = DEFS.find((d) => d.id === id), ind = DATA.indicators[id];
    if (!def || !ind) return;
    destroyCharts();
    if (!fromHash) history.replaceState(null, '', '#' + id);
    const on = new Set(def.views.filter((v) => v.on && ind.series[v.s]).map((v) => v.s));
    state = { def, ind, on, years: def.defaultYears || 10, table: false };
    inner.innerHTML = buildDetailHtml(def, ind);
    inner.querySelector('.d-close').addEventListener('click', () => dlg.close());
    inner.querySelectorAll('[data-years]').forEach((b) => b.addEventListener('click', () => { state.years = +b.dataset.years; syncPills(); drawChart(); }));
    inner.querySelectorAll('[data-series]').forEach((b) => b.addEventListener('click', () => toggleSeries(b.dataset.series)));
    inner.querySelector('[data-table]').addEventListener('click', () => { state.table = !state.table; syncPills(); drawTable(); });
    inner.querySelector('[data-csv]').addEventListener('click', downloadCsv);
    if (!dlg.open) dlg.showModal();
    syncPills();
    drawChart();
    drawTable();
    if (def.second) drawSecond();
    dlg.scrollTop = 0;
  }

  // Series of different frequencies cannot share one category axis: switching one on drops the others
  function toggleSeries(key) {
    const freq = state.ind.series[key].freq;
    if (state.on.has(key)) { if (state.on.size > 1) state.on.delete(key); }
    else { for (const k of [...state.on]) if (state.ind.series[k].freq !== freq) state.on.delete(k); state.on.add(key); }
    syncPills(); drawChart();
  }

  const viewOf = (key) => state.def.views.find((v) => v.s === key);
  const viewLabel = (v, s) => v.short || (s ? s.label.split(/[,(]/)[0].trim() : v.s);

  function buildDetailHtml(def, ind) {
    const hs = ind.series[def.headline.series];
    const L = last(hs), P = last(hs, 2);
    const stats = [
      { k: 'Latest', v: def.headline.fmt(L.v), s: periodLabel(L.p, hs.freq) },
      { k: 'Previous', v: def.headline.fmt(P.v), s: periodLabel(P.p, hs.freq) },
      ...(def.secondary || []).map((x) => { const ss = ind.series[x.series]; if (!ss) return null; const l = last(ss); return { k: x.label, v: x.fmt(l.v), s: periodLabel(l.p, ss.freq) }; }).filter(Boolean),
    ];
    const ranges = def.defaultYears >= 30 ? [10, 20, 30, 100] : [5, 10, 20, 100];
    const pills = def.views.filter((v) => ind.series[v.s]).map((v, i) =>
      `<button class="pill" type="button" data-series="${v.s}" aria-pressed="false" title="${ind.series[v.s].label}"><span class="sw" style="background:var(${SLOTS[def.views.indexOf(v) % 8]})"></span>${viewLabel(v, ind.series[v.s])}</button>`).join('');
    const sources = (ind.sources || []).map((s) => `<li>${s.url ? `<a href="${s.url}">${s.name}</a>` : s.name}${s.table ? ` (${/^\d/.test(s.table) ? 'table ' : 'dataflow '}${s.table})` : ''}${s.updated ? `, updated ${s.updated}` : ''}${s.note ? `. ${s.note}` : ''}</li>`).join('');
    const derived = Object.values(ind.series).filter((s) => s.derived).map((s) => `<li>${s.label}${s.note ? ': ' + s.note : ''}</li>`).join('');
    return `
      <div class="d-top">
        <div><div class="kicker">${def.kicker}</div><h2 id="d-title">${def.title}</h2></div>
        <button class="d-close" type="button" aria-label="Close">✕</button>
      </div>
      <p class="d-desc">${def.desc}</p>
      <div class="d-stats">${stats.map((s) => `<div class="st"><div class="k">${s.k}</div><div class="v">${s.v}<small>${s.s}</small></div></div>`).join('')}</div>
      <div class="d-controls">
        <div class="group"><span class="label">Range</span>${ranges.map((y) => `<button class="pill" type="button" data-years="${y}">${y >= 100 ? 'Max' : y + ' y'}</button>`).join('')}</div>
        <div class="group"><span class="label">Series</span>${pills}</div>
        <div class="group"><button class="pill" type="button" data-table aria-pressed="false">Table</button><button class="pill" type="button" data-csv>Download CSV</button></div>
      </div>
      <div class="d-chart"><canvas id="d-canvas" role="img" aria-label="${def.title} chart"></canvas></div>
      <div class="d-legend" id="d-legend"></div>
      <div class="d-table-wrap" id="d-table" hidden></div>
      ${def.second ? `<div class="d-second"><h3>${def.second.title}</h3><div class="d-chart"><canvas id="d-canvas2" role="img" aria-label="${def.second.title}"></canvas></div></div>` : ''}
      ${def.decisions && ind.decisions ? decisionsHtml(ind.decisions) : ''}
      <div class="d-notes">
        ${def.showMethod && ind.method ? `<h4>Method</h4><p class="method">${ind.method}</p>` : ''}
        ${derived ? `<h4>Derived series (own calculations, marked * in legends)</h4><ul>${derived}</ul>` : ''}
        <h4>Sources</h4><ul>${sources}</ul>
        ${ind.stale ? `<p class="warn">The last automatic update of this indicator failed (${ind.error}); figures are from the previous successful fetch.</p>` : ''}
      </div>`;
  }

  function decisionsHtml(dec) {
    const rows = dec.slice(-12).reverse().map((d) => `<tr><td>${periodLabel(d.date, 'D')}</td><td>${fmt.num(d.rate, 2)} %</td><td class="chg ${d.change > 0 ? 'up' : d.change < 0 ? 'down' : ''}">${d.change > 0 ? '+' : ''}${fmt.num(d.change, 2)}</td></tr>`).join('');
    return `<div class="d-notes"><h4>Recent policy rate decisions (announcement date, new rate, change)</h4><table class="decisions"><tbody>${rows}</tbody></table></div>`;
  }

  function syncPills() {
    inner.querySelectorAll('[data-years]').forEach((b) => b.setAttribute('aria-pressed', String(+b.dataset.years === state.years)));
    inner.querySelectorAll('[data-series]').forEach((b) => b.setAttribute('aria-pressed', String(state.on.has(b.dataset.series))));
    inner.querySelector('[data-table]').setAttribute('aria-pressed', String(state.table));
  }

  // Aligned period axis for the selected series within the chosen range
  function alignedData() {
    const views = state.def.views.filter((v) => state.on.has(v.s));
    const sers = views.map((v) => state.ind.series[v.s]);
    const endT = Math.max(...sers.map((s) => periodTime(s.periods[s.periods.length - 1])));
    const startT = state.years >= 100 ? -Infinity : endT - state.years + 1 / 12;
    const periodSet = new Map();
    sers.forEach((s) => s.periods.forEach((p) => { const t = periodTime(p); if (t >= startT) periodSet.set(p, t); }));
    const periods = [...periodSet.keys()].sort((a, b) => periodSet.get(a) - periodSet.get(b));
    const datasets = views.map((v) => {
      const s = state.ind.series[v.s];
      const map = new Map(s.periods.map((p, i) => [p, s.values[i]]));
      const scale = (state.def.scale && s.unit.startsWith('NOK million')) ? state.def.scale : 1;
      return { v, s, slot: state.def.views.indexOf(v), scale, label: viewLabel(v, s),
               unit: scale !== 1 ? state.def.unitLabel : s.unit,
               data: periods.map((p) => (map.has(p) && map.get(p) != null ? map.get(p) * scale : null)) };
    });
    return { periods, datasets };
  }

  const crosshair = {
    id: 'crosshair',
    afterDraw(c) {
      const a = c.tooltip && c.tooltip.getActiveElements();
      if (!a || !a.length) return;
      const x = a[0].element.x, { top, bottom } = c.chartArea, ctx = c.ctx;
      ctx.save(); ctx.strokeStyle = cssVar('--viz-axis'); ctx.lineWidth = 1; ctx.setLineDash([3, 3]);
      ctx.beginPath(); ctx.moveTo(x, top); ctx.lineTo(x, bottom); ctx.stroke(); ctx.restore();
    },
  };
  const targetLine = {
    id: 'targetLine',
    afterDatasetsDraw(c, args, opts) {
      if (opts.value == null) return;
      const y = c.scales.y.getPixelForValue(opts.value), { left, right } = c.chartArea, ctx = c.ctx;
      ctx.save(); ctx.strokeStyle = cssVar('--viz-muted'); ctx.setLineDash([6, 4]); ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(left, y); ctx.lineTo(right, y); ctx.stroke();
      ctx.fillStyle = cssVar('--viz-muted'); ctx.font = '11px Inter, system-ui, sans-serif'; ctx.textAlign = 'right';
      ctx.fillText(opts.label, right - 4, y - 4); ctx.restore();
    },
  };

  const axisLabel = (p) => {
    const f = freqOf(p);
    if (f === 'A') return p;
    if (f === 'Q') return p.endsWith('Q1') ? p.slice(0, 4) : p.replace('-', ' ');
    if (f === 'M') { const [y, m] = p.split('-'); return m === '01' ? y : MONTHS[+m - 1] + ' ' + y.slice(2); }
    const [y, m, d] = p.split('-'); return `${+d} ${MONTHS[+m - 1]} ${y.slice(2)}`;
  };
  const unitShort = (u) => (!u || u === 'index' ? '' : u.replace('per cent of GDP', '% of GDP').replace('per cent of labour force', '%').replace('per cent of population', '%').replace('per cent', '%'));

  function baseOptions(periods, unit, extraPlugins) {
    const ink = cssVar('--viz-ink-2'), grid = cssVar('--viz-grid');
    const tickEvery = Math.max(1, Math.round(periods.length / 9));
    return {
      responsive: true, maintainAspectRatio: false, animation: false, normalized: true,
      interaction: { mode: 'index', intersect: false },
      layout: { padding: { top: 6, right: 8 } },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: cssVar('--viz-surface'), titleColor: cssVar('--viz-ink'), bodyColor: ink, borderColor: grid, borderWidth: 1, padding: 10,
          callbacks: {
            title: (items) => (items.length ? periodLabel(items[0].label, freqOf(items[0].label)) : ''),
            label: (it) => ` ${it.dataset.label}: ${it.parsed.y == null ? '–' : fmt.num(it.parsed.y, it.dataset._dec)}${it.dataset._unit ? ' ' + it.dataset._unit : ''}`,
          },
        },
        ...extraPlugins,
      },
      scales: {
        x: { grid: { display: false }, border: { color: cssVar('--viz-axis') },
             ticks: { color: ink, maxRotation: 0, autoSkip: false, font: { size: 11 }, callback(v, i) { return i % tickEvery === 0 ? axisLabel(periods[i]) : ''; } } },
        y: { grid: { color: grid }, border: { display: false },
             ticks: { color: ink, font: { size: 11 }, callback: (v) => fmt.num(v, Math.abs(v) < 10 && v !== Math.round(v) ? 1 : 0) },
             title: { display: !!unit, text: unit, color: ink, font: { size: 11 } } },
      },
    };
  }

  function drawChart() {
    const { periods, datasets } = alignedData();
    const units = [...new Set(datasets.map((d) => d.unit))];
    const unit = units.length === 1 ? units[0] : '';
    const ds = datasets.map((d) => {
      const color = cssVar(SLOTS[d.slot % 8]);
      const base = { label: d.label, data: d.data, _dec: d.s.decimals, _unit: unitShort(d.unit) };
      if (d.v.kind === 'bar') return { ...base, type: 'bar', backgroundColor: color, borderRadius: 2, borderSkipped: 'start', barPercentage: 0.9, categoryPercentage: 0.9, order: 2 };
      return { ...base, type: 'line', borderColor: color, backgroundColor: color, borderWidth: 2, pointRadius: 0, pointHoverRadius: 4, pointHitRadius: 10, spanGaps: periods.length < 400, tension: 0.15, order: 1 };
    });
    const showTarget = state.def.target && datasets.some((d) => ['cpi_yoy', 'cpiate_yoy', 'cpiat_yoy'].includes(d.v.s)) && datasets.every((d) => ['cpi_yoy', 'cpiate_yoy', 'cpiat_yoy'].includes(d.v.s));
    const opts = baseOptions(periods, unit, { crosshair: {}, targetLine: showTarget ? { value: state.def.target, label: 'Target 2 %' } : {} });
    const emphasiseZero = datasets.some((d) => d.v.kind === 'bar' || d.data.some((v) => v != null && v < 0));
    if (emphasiseZero) opts.scales.y.grid.color = (ctx) => (ctx.tick.value === 0 ? cssVar('--viz-axis') : cssVar('--viz-grid'));
    const ctx = document.getElementById('d-canvas').getContext('2d');
    if (chart) chart.destroy();
    chart = new Chart(ctx, { type: 'line', data: { labels: periods, datasets: ds }, options: opts, plugins: [crosshair, targetLine] });
    document.getElementById('d-legend').innerHTML = datasets.map((d) =>
      `<span><span class="sw ${d.v.kind}" style="background:var(${SLOTS[d.slot % 8]})"></span>${d.s.label}${d.s.derived ? ' *' : ''}</span>`).join('');
    if (state.table) drawTable();
  }

  function drawSecond() {
    const sec = state.def.second;
    const sers = sec.series.map((k) => state.ind.series[k]).filter(Boolean);
    const labels = sers.map((s) => s.label.split(',')[0]);
    const vals = sers.map((s) => last(s).v);
    const periodTxt = periodLabel(last(sers[0]).p, sers[0].freq);
    const ctx = document.getElementById('d-canvas2').getContext('2d');
    const ink = cssVar('--viz-ink-2'), grid = cssVar('--viz-grid');
    chart2 = new Chart(ctx, {
      type: 'bar',
      data: { labels, datasets: [{ label: `12-month change, ${periodTxt}`, data: vals, backgroundColor: cssVar('--s1'), borderRadius: 3, borderSkipped: 'start', barPercentage: 0.7 }] },
      options: {
        indexAxis: 'y', responsive: true, maintainAspectRatio: false, animation: false,
        plugins: { legend: { display: false }, tooltip: { callbacks: { label: (it) => ` ${fmt.num(it.parsed.x, 1)} % (${periodTxt})` } } },
        scales: { x: { grid: { color: (c) => (c.tick.value === 0 ? cssVar('--viz-axis') : grid) }, ticks: { color: ink, callback: (v) => v + ' %' }, border: { display: false } },
                  y: { grid: { display: false }, ticks: { color: ink, font: { size: 11 } }, border: { color: cssVar('--viz-axis') } } },
      },
    });
  }

  function drawTable() {
    const wrap = document.getElementById('d-table');
    wrap.hidden = !state.table;
    if (!state.table) return;
    const { periods, datasets } = alignedData();
    const head = `<tr><th>Period</th>${datasets.map((d) => `<th>${d.label}${unitShort(d.unit) ? ` (${unitShort(d.unit)})` : ''}</th>`).join('')}</tr>`;
    const rows = [...periods].reverse().map((p, ri) => { const i = periods.length - 1 - ri; return `<tr><td>${periodLabel(p, freqOf(p))}</td>${datasets.map((d) => `<td>${d.data[i] == null ? '' : fmt.num(d.data[i], d.s.decimals)}</td>`).join('')}</tr>`; }).join('');
    wrap.innerHTML = `<div class="d-table-scroll"><table class="d-table"><thead>${head}</thead><tbody>${rows}</tbody></table></div>`;
  }

  function downloadCsv() {
    const { periods, datasets } = alignedData();
    const q = (t) => '"' + String(t).replace(/"/g, '""') + '"';
    const lines = ['period,' + datasets.map((d) => q(d.s.label + (d.unit ? ' [' + d.unit + ']' : ''))).join(',')];
    periods.forEach((p, i) => lines.push(p + ',' + datasets.map((d) => (d.data[i] == null ? '' : d.data[i])).join(',')));
    lines.push('', q('Sources: ' + (state.ind.sources || []).map((s) => s.name + (s.table ? ' ' + s.table : '')).join('; ')));
    const blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8' });
    const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = `norway-${state.def.id}.csv`; a.click(); URL.revokeObjectURL(a.href);
  }
})();
