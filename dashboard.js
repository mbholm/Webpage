/* Norway dashboard: renders data/dashboard.json (built by build_dashboard_data.py).
   Display definitions live here, keyed by the same indicator ids as the build script. */
(function () {
  'use strict';

  // ------------------------------------------------------------------ formatting helpers
  const fmt = {
    num(v, d) { return v == null ? '–' : v.toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d }); },
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
    if (freq === 'D') { const [y, m, d] = p.split('-'); return `${MONTHS[+m - 1]} ${+d}, ${y}`; }
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
  // headline: the series carried by the tile; goodUp: true/false colors the change as good/bad, null keeps it neutral
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
      desc: 'Twelve-month change in the consumer price index (CPI) and in CPI-ATE, the index adjusted for tax changes and excluding energy products that Norges Bank uses as its core measure. The inflation target is 2 percent. Dashed lines are Norges Bank’s quarterly projections from the latest Monetary Policy Report.',
      headline: { series: 'cpi_yoy', fmt: (v) => fmt.pct(v), sub: 'CPI, 12-month change' },
      secondary: [{ series: 'cpiate_yoy', label: 'CPI-ATE', fmt: fmt.pct }],
      spark: 'cpi_yoy', sparkZero: true, target: 2,
      views: [{ s: 'cpi_yoy', short: 'CPI', kind: 'line', on: true }, { s: 'cpiate_yoy', short: 'CPI-ATE', kind: 'line', on: true }, { s: 'cpiat_yoy', short: 'CPI-AT', kind: 'line' },
              { s: 'cpi_mom', short: 'CPI, m/m', kind: 'bar' }, { s: 'cpi_index', short: 'CPI index', kind: 'line' },
              { s: 'proj_cpi', short: 'CPI, NB projection', kind: 'line', on: true, tint: 'cpi_yoy' }, { s: 'proj_cpiate', short: 'CPI-ATE, NB projection', kind: 'line', on: true, tint: 'cpiate_yoy' }],
      second: { title: 'Main CPI groups, 12-month change', series: ['grp_01', 'grp_04', 'grp_07', 'grp_11', 'grp_09', 'grp_03', 'grp_05', 'grp_08'] },
      defaultYears: 10,
    },
    {
      id: 'unemployment', title: 'Unemployment', kicker: 'Labor market', goodUp: false,
      desc: 'Two measures of unemployment: the Labor Force Survey (LFS) rate for ages 15–74 (seasonally adjusted three-month moving average dated by the middle month) and NAV’s registered unemployment, i.e. people registered as fully unemployed as a share of the labor force. NAV’s gross measure adds participants in labor market programs. Quarterly LFS series give finer breakdowns by age and sex.',
      headline: { series: 'unemployment', fmt: (v) => fmt.pct(v), sub: 'LFS unemployment rate, 3-month average' },
      secondary: [{ series: 'nav_unemployment', label: 'registered (NAV)', fmt: fmt.pct }],
      spark: 'unemployment',
      views: [{ s: 'unemployment', short: 'LFS', kind: 'line', on: true, group: 'Monthly' }, { s: 'nav_unemployment', short: 'Registered (NAV)', kind: 'line', on: true, group: 'Monthly' },
              { s: 'nav_gross', short: 'Registered incl. programs', kind: 'line', group: 'Monthly' }, { s: 'nav_unemployment_nsa', short: 'Registered, unadjusted (1991–)', kind: 'line', group: 'Monthly' },
              { s: 'nav_count', short: 'Registered, persons', kind: 'line', group: 'Monthly' }, { s: 'unemployed', short: 'LFS, persons', kind: 'line', group: 'Monthly' },
              { s: 'unemployment_men', short: 'Men', kind: 'line', group: 'Monthly LFS by sex and age' }, { s: 'unemployment_women', short: 'Women', kind: 'line', group: 'Monthly LFS by sex and age' },
              { s: 'unemployment_15_24', short: '15–24', kind: 'line', group: 'Monthly LFS by sex and age' }, { s: 'unemployment_25_74', short: '25–74', kind: 'line', group: 'Monthly LFS by sex and age' },
              { s: 'q_unemployment_25_54', short: '25–54', kind: 'line', group: 'Quarterly LFS' }, { s: 'q_unemployment_55_74', short: '55–74', kind: 'line', group: 'Quarterly LFS' },
              { s: 'q_unemployment_men_15_24', short: 'Men 15–24', kind: 'line', group: 'Quarterly LFS' }, { s: 'q_unemployment_women_15_24', short: 'Women 15–24', kind: 'line', group: 'Quarterly LFS' }],
      defaultYears: 10,
    },
    {
      id: 'employment', title: 'Employment', kicker: 'Labor market', goodUp: true,
      desc: 'Employed persons as a share of the population, from the Labor Force Survey (LFS). Monthly series are seasonally adjusted three-month moving averages dated by the middle month. The quarterly series break the employment rate down by age group and sex. Participation is the labor force as a share of the population.',
      headline: { series: 'employment_rate', fmt: (v) => fmt.pct(v), sub: 'employment rate, 15–74, 3-month average' },
      secondary: [{ series: 'participation', label: 'participation', fmt: fmt.pct }],
      spark: 'employment_rate',
      views: [{ s: 'employment_rate', short: 'Employment rate', kind: 'line', on: true, group: 'Monthly' }, { s: 'participation', short: 'Participation rate', kind: 'line', group: 'Monthly' },
              { s: 'employment_rate_men', short: 'Men', kind: 'line', group: 'Monthly' }, { s: 'employment_rate_women', short: 'Women', kind: 'line', group: 'Monthly' },
              { s: 'employment_rate_15_24', short: '15–24', kind: 'line', group: 'Monthly' }, { s: 'employment_rate_25_74', short: '25–74', kind: 'line', group: 'Monthly' },
              { s: 'employed', short: 'Employed, persons', kind: 'line', group: 'Monthly' }, { s: 'manweeks', short: 'Man-weeks worked', kind: 'line', group: 'Monthly' },
              { s: 'q_rate_15_24', short: '15–24', kind: 'line', group: 'Quarterly, by age' }, { s: 'q_rate_25_39', short: '25–39', kind: 'line', group: 'Quarterly, by age' },
              { s: 'q_rate_40_54', short: '40–54', kind: 'line', group: 'Quarterly, by age' }, { s: 'q_rate_55_74', short: '55–74', kind: 'line', group: 'Quarterly, by age' },
              { s: 'q_rate_20_64', short: '20–64', kind: 'line', group: 'Quarterly, by age' },
              { s: 'q_rate_men_15_24', short: 'Men 15–24', kind: 'line', group: 'Quarterly, by age and sex' }, { s: 'q_rate_women_15_24', short: 'Women 15–24', kind: 'line', group: 'Quarterly, by age and sex' },
              { s: 'q_rate_men_25_54', short: 'Men 25–54', kind: 'line', group: 'Quarterly, by age and sex' }, { s: 'q_rate_women_25_54', short: 'Women 25–54', kind: 'line', group: 'Quarterly, by age and sex' },
              { s: 'q_rate_men_55_74', short: 'Men 55–74', kind: 'line', group: 'Quarterly, by age and sex' }, { s: 'q_rate_women_55_74', short: 'Women 55–74', kind: 'line', group: 'Quarterly, by age and sex' }],
      defaultYears: 10,
    },
    {
      id: 'tightness', title: 'Labor market tightness', kicker: 'Labor market', goodUp: null,
      desc: 'Job vacancies relative to the number of unemployed. A higher ratio means more jobs chasing fewer job seekers. Vacancies are the stock of unfilled jobs from Statistics Norway’s quarterly vacancy survey, which starts in 2010. The unemployed are counted in two ways: by the Labor Force Survey and by registration with NAV. The Beveridge curves below the chart plot the vacancy rate against the unemployment rate.',
      headline: { series: 'vu_lfs', fmt: (v) => fmt.num(v, 2), sub: 'vacancies per unemployed person (Labor Force Survey)' },
      secondary: [{ series: 'vu_nav', label: 'per registered unemployed (NAV)', fmt: (v) => fmt.num(v, 2) }],
      spark: 'vu_lfs',
      views: [{ s: 'vu_lfs', short: 'Labor Force Survey unemployed', kind: 'line', on: true, group: 'Vacancies per unemployed person' }, { s: 'vu_nav', short: 'Registered unemployed (NAV)', kind: 'line', on: true, group: 'Vacancies per unemployed person' },
              { s: 'vacancies', short: 'Vacancies', kind: 'line', group: 'Vacancies' }, { s: 'vacancy_rate', short: 'Vacancy rate', kind: 'line', group: 'Vacancies' },
              { s: 'unemployed_lfs', short: 'Labor Force Survey', kind: 'line', group: 'Unemployed persons' }, { s: 'unemployed_nav', short: 'Registered (NAV)', kind: 'line', group: 'Unemployed persons' }],
      defaultYears: 20,
    },
    {
      id: 'rates', title: 'Interest rates', kicker: 'Monetary policy', goodUp: null,
      desc: 'Norges Bank’s policy rate (the sight deposit rate), the NOWA overnight money-market rate and generic government yields. Monthly averages give the long history. The daily series cover recent years and cannot be mixed with the monthly ones in the same chart. The dashed line is Norges Bank’s own policy rate forecast from the latest Monetary Policy Report.',
      headline: { series: 'policy_rate_daily', fmt: (v) => fmt.pct(v, 2), sub: 'policy rate' },
      secondary: [{ series: 'gov10y_daily', label: '10-year yield', fmt: (v) => fmt.pct(v, 2) }],
      spark: 'policy_rate',
      views: [{ s: 'policy_rate', short: 'Policy rate', kind: 'line', on: true }, { s: 'gov10y', short: '10-year yield', kind: 'line', on: true }, { s: 'gov3y', short: '3-year yield', kind: 'line' },
              { s: 'tbill3m', short: '3-month T-bill', kind: 'line' }, { s: 'nowa', short: 'NOWA', kind: 'line' },
              { s: 'policy_rate_daily', short: 'Policy rate, daily', kind: 'line' }, { s: 'gov10y_daily', short: '10-year yield, daily', kind: 'line' },
              { s: 'proj_policy_rate', short: 'Policy rate, NB projection', kind: 'line', on: true, tint: 'policy_rate' }],
      decisions: true, defaultYears: 10,
    },
    {
      id: 'fx', title: 'Exchange rates', kicker: 'The krone', goodUp: null,
      desc: 'The import-weighted krone index I-44 (higher means a weaker krone) and the krone against the euro and the US dollar. Monthly averages give the long history. The daily series cover recent years.',
      headline: { series: 'i44_daily', fmt: (v) => fmt.plain(v, 1), sub: 'I-44 index, 1995 = 100 (higher = weaker krone)' },
      secondary: [{ series: 'eurnok_daily', label: 'EUR', fmt: (v) => fmt.plain(v, 2) }, { series: 'usdnok_daily', label: 'USD', fmt: (v) => fmt.plain(v, 2) }],
      spark: 'i44',
      views: [{ s: 'i44', short: 'I-44', kind: 'line', on: true }, { s: 'eurnok', short: 'EUR/NOK', kind: 'line' }, { s: 'usdnok', short: 'USD/NOK', kind: 'line' }, { s: 'gbpnok', short: 'GBP/NOK', kind: 'line' }, { s: 'seknok', short: 'SEK/NOK (per 100)', kind: 'line' },
              { s: 'i44_daily', short: 'I-44, daily', kind: 'line' }, { s: 'eurnok_daily', short: 'EUR/NOK, daily', kind: 'line' }, { s: 'usdnok_daily', short: 'USD/NOK, daily', kind: 'line' }],
      defaultYears: 10,
    },
    {
      id: 'oil_fund', title: 'The oil fund', kicker: 'Government Pension Fund Global', goodUp: true,
      desc: 'Market value of the Government Pension Fund Global at year-end, by asset class, as published by NBIM. The current year shows the latest value NBIM has published, not a year-end value. The asset classes are also shown as shares of the fund. Returns are measured in the fund’s currency basket before management costs, and the relative return is the fund’s return minus the return on its benchmark index. For the current year the return covers the period NBIM has reported so far. Management costs are the annual costs of running the fund, as a share of assets under management and in kroner.',
      headline: { series: 'value', fmt: (v) => fmt.trnFromBn(v), sub: 'market value', latestLabel: 'latest' },
      secondary: [{ series: 'pct_gdp', label: 'of GDP', fmt: (v) => fmt.pct(v, 0) }],
      spark: 'value', unitLabel: 'NOK bn',
      views: [{ s: 'value', short: 'Total', kind: 'line', on: true, group: 'Market value' }, { s: 'equity', short: 'Equity', kind: 'line', group: 'Market value' },
              { s: 'fixed_income', short: 'Fixed income', kind: 'line', group: 'Market value' }, { s: 'real_estate', short: 'Real estate', kind: 'line', group: 'Market value' },
              { s: 'renewables', short: 'Renewable infrastructure', kind: 'line', group: 'Market value' }, { s: 'pct_gdp', short: 'Total, % of GDP', kind: 'bar', group: 'Market value' },
              { s: 'share_equity', short: 'Equity', kind: 'line', group: 'Share of the fund' }, { s: 'share_fixed_income', short: 'Fixed income', kind: 'line', group: 'Share of the fund' },
              { s: 'share_real_estate', short: 'Real estate', kind: 'line', group: 'Share of the fund' }, { s: 'share_renewables', short: 'Renewable infrastructure', kind: 'line', group: 'Share of the fund' },
              { s: 'return', short: 'Fund, annual', kind: 'bar', group: 'Return' }, { s: 'return_accum', short: 'Fund, annualized since 1998', kind: 'line', group: 'Return' },
              { s: 'return_equity', short: 'Equity', kind: 'line', group: 'Return' }, { s: 'return_fixed_income', short: 'Fixed income', kind: 'line', group: 'Return' },
              { s: 'return_real_estate', short: 'Real estate', kind: 'line', group: 'Return' }, { s: 'return_renewables', short: 'Renewable infrastructure', kind: 'line', group: 'Return' },
              { s: 'relative_return', short: 'Annual', kind: 'bar', group: 'Relative return against the benchmark' }, { s: 'relative_return_accum', short: 'Annualized since 1998', kind: 'line', group: 'Relative return against the benchmark' },
              { s: 'cost_share', short: 'Share of the fund', kind: 'bar', group: 'Management costs' }, { s: 'costs', short: 'NOK million', kind: 'bar', group: 'Management costs' }],
      defaultYears: 30,
    },
    {
      id: 'budget', title: 'Government budget', kicker: 'Fiscal policy', goodUp: null,
      desc: 'Fiscal policy as presented in the latest National Budget (Nasjonalbudsjettet). The structural non-oil deficit is the cyclically adjusted budget deficit excluding petroleum revenue. It measures how much oil money the budget uses and is covered by transfers from the oil fund. Under the fiscal rule this spending should over time follow the expected real return on the fund, set at 3 percent. Actual spending is the oil-corrected deficit, which is not adjusted for the business cycle, as a share of the fund at the start of the year. Below the chart are the state’s spending and income in the budget proposal.',
      headline: { series: 'deficit_gdp', fmt: (v) => fmt.pct(v), sub: 'structural non-oil deficit, share of trend mainland GDP' },
      secondary: [{ series: 'fund_pct_actual', label: 'actual oil spending, share of the fund', fmt: fmt.pct }],
      spark: 'deficit_gdp',
      views: [{ s: 'deficit_gdp', short: 'Structural non-oil deficit', kind: 'line', on: true, group: 'Deficit, share of trend mainland GDP' },
              { s: 'oil_corrected_gdp', short: 'Oil-corrected deficit', kind: 'line', group: 'Deficit, share of trend mainland GDP' },
              { s: 'fund_pct_actual', short: 'Actual spending, share of the fund', kind: 'line', group: 'Use of the oil fund' }, { s: 'fund_pct', short: 'Structural spending, share of the fund', kind: 'line', group: 'Use of the oil fund' }, { s: 'expected_return', short: 'Expected real return', kind: 'line', group: 'Use of the oil fund' },
              { s: 'fund_share_budget', short: 'Fund spending, share of expenditure', kind: 'line', group: 'Use of the oil fund' },
              { s: 'oil_corrected_nok', short: 'Oil-corrected deficit', kind: 'line', group: 'Deficit, NOK billion' }, { s: 'deficit_nok', short: 'Structural non-oil deficit', kind: 'line', group: 'Deficit, NOK billion' },
              { s: 'impulse', short: 'Budget indicator', kind: 'bar', group: 'Other' }, { s: 'public_expenditure', short: 'Public expenditure, share of mainland GDP', kind: 'line', group: 'Other' },
              { s: 'tax_revenue', short: 'Taxes, share of mainland GDP', kind: 'line', group: 'Other' }],
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
      id: 'credit', title: 'Credit', kicker: 'Debt', goodUp: null,
      desc: 'Twelve-month growth in domestic loan debt, Statistics Norway’s credit indicator (C2), for households, non-financial corporations and municipalities. The debt ratio is household debt as a share of disposable income, from the financial accounts.',
      headline: { series: 'growth_households', fmt: (v) => fmt.pct(v), sub: 'household debt, 12-month growth' },
      secondary: [{ series: 'growth_total', label: 'all sectors (C2)', fmt: fmt.pct }],
      spark: 'growth_households', scale: 1 / 1000, unitLabel: 'NOK bn',
      views: [{ s: 'growth_households', short: 'Households', kind: 'line', on: true, group: '12-month growth' }, { s: 'growth_corporations', short: 'Non-financial corporations', kind: 'line', on: true, group: '12-month growth' },
              { s: 'growth_municipalities', short: 'Municipalities', kind: 'line', group: '12-month growth' }, { s: 'growth_total', short: 'All sectors (C2)', kind: 'line', on: true, group: '12-month growth' },
              { s: 'debt_households', short: 'Households', kind: 'line', group: 'Debt outstanding' }, { s: 'debt_corporations', short: 'Non-financial corporations', kind: 'line', group: 'Debt outstanding' },
              { s: 'debt_municipalities', short: 'Municipalities', kind: 'line', group: 'Debt outstanding' }, { s: 'debt_total', short: 'All sectors (C2)', kind: 'line', group: 'Debt outstanding' },
              { s: 'debt_income', short: 'Debt, share of disposable income', kind: 'line', group: 'Household debt ratio' }],
      defaultYears: 20,
    },
    {
      id: 'productivity', title: 'Productivity', kicker: 'Supply side', goodUp: true,
      desc: 'Labor productivity (value added per hour worked) and total factor productivity in Mainland Norway, from the annual national accounts. TFP growth is computed by growth accounting, weighting hours and capital by the labor share observed in the national accounts (average of adjacent years), and then cumulated into an index.',
      headline: { series: 'tfp_growth', fmt: (v) => fmt.signedPct(v), sub: 'total factor productivity growth' },
      secondary: [{ series: 'labor_productivity_growth', label: 'labor productivity growth', fmt: fmt.signedPct }],
      spark: 'tfp_growth', sparkZero: true,
      views: [{ s: 'labor_productivity', short: 'Labor productivity', kind: 'line', on: true }, { s: 'tfp', short: 'TFP', kind: 'line', on: true }, { s: 'capital_per_hour', short: 'Capital per hour', kind: 'line' },
              { s: 'labor_productivity_growth', short: 'Labor productivity growth', kind: 'bar' }, { s: 'tfp_growth', short: 'TFP growth', kind: 'bar' },
              { s: 'output_growth', short: 'Value added growth', kind: 'line' }, { s: 'hours_growth', short: 'Hours growth', kind: 'line' }, { s: 'capital_growth', short: 'Capital growth', kind: 'line' },
              { s: 'labor_share', short: 'Labor share (weight)', kind: 'line' }],
      defaultYears: 30, showMethod: true,
    },
    {
      id: 'wages', title: 'Wages', kicker: 'Earnings', goodUp: null,
      desc: 'Annual earnings for all employees, per full-time equivalent, from Statistics Norway: nominal growth, real growth (deflated by the consumer price index) and the level in kroner, with growth by industry from 2017.',
      headline: { series: 'wage_growth', fmt: (v) => fmt.pct(v), sub: 'annual earnings growth' },
      secondary: [{ series: 'real_wage_growth', label: 'real', fmt: fmt.signedPct }],
      spark: 'wage_growth', sparkZero: true,
      views: [{ s: 'wage_growth', short: 'Nominal growth', kind: 'line', on: true, group: 'Annual earnings' }, { s: 'real_wage_growth', short: 'Real growth', kind: 'line', on: true, group: 'Annual earnings' },
              { s: 'wage_level', short: 'Level, NOK', kind: 'line', group: 'Annual earnings' },
              ...[['AS', 'All industries'], ['C', 'Manufacturing'], ['F', 'Construction'], ['G', 'Trade'], ['H', 'Transport'], ['J', 'Information and communication'], ['K', 'Finance'],
                  ['M', 'Professional services'], ['O', 'Public administration'], ['P', 'Education'], ['Q', 'Health and social work']]
                .map(([k, t]) => ({ s: 'wg_' + k, short: t, kind: 'line', group: 'Earnings growth by industry (2017–)' }))],
      defaultYears: 30,
    },
    {
      id: 'labor_shares', title: 'Labor shares', kicker: 'Income distribution', goodUp: null,
      desc: 'The share of factor income that goes to labor, by industry, computed from the annual national accounts. The gross labor share divides compensation of employees by compensation plus consumption of fixed capital plus net operating surplus. The net labor share leaves capital consumption out of the denominator. The headline is the net labor share in manufacturing. The common component for manufacturing is the first principal component of the sectoral labor shares, computed in real time as in Holm and Jasinski (Samfunnsøkonomen), and centered on the same average as the manufacturing share. Dashed lines show each series’ average over the past 30 years.',
      headline: { series: 'net_mfg', fmt: (v) => fmt.pct(v), sub: 'net labor share, manufacturing' },
      secondary: [{ series: 'gross_mfg', label: 'gross', fmt: fmt.pct }],
      spark: 'net_mfg',
      views: ['gross', 'net'].flatMap((g) => [['mfg', 'Manufacturing'], ['pc', 'Manufacturing, common component'], ['business', 'Mainland excl. government'], ['mainland', 'Mainland Norway'], ['total', 'Total economy'], ['oilgas', 'Oil and gas'],
                  ['construction', 'Construction'], ['trade', 'Trade'], ['transport', 'Transport'], ['accommodation', 'Accommodation and food'], ['ict', 'Information and communication'],
                  ['finance', 'Finance'], ['professional', 'Professional services']]
                .map(([k, t]) => ({ s: `${g}_${k}`, short: t, tip: `${g === 'gross' ? 'Gross' : 'Net'}, ${t}`, avgFrom: k === 'pc' ? `${g}_mfg` : null, kind: 'line', on: k === 'mfg', group: g === 'gross' ? 'Gross labor share' : 'Net labor share' }))),
      defaultYears: 30, showMethod: true, avgYears: 30,
    },
    {
      id: 'inequality', title: 'Inequality', kicker: 'Income and wealth distribution', goodUp: null,
      desc: 'Income inequality is measured by the Gini coefficient for household income after tax per consumption unit, where 0 is full equality and 1 is one person having everything, and by two ratios between high and low incomes. Wealth inequality is shown as shares of total net wealth held by the richest households and by the bottom half. Long-run top shares come from the World Inequality Database and use other definitions than Statistics Norway.',
      headline: { series: 'gini', fmt: (v) => fmt.num(v, 3), sub: 'Gini coefficient, income after tax' },
      secondary: [{ series: 'wealth_top1', label: 'top 1 % share of net wealth', fmt: fmt.pct }],
      spark: 'gini',
      views: [{ s: 'gini', short: 'Gini coefficient', kind: 'line', on: true, group: 'Income after tax, Statistics Norway' }, { s: 'gini_all', short: 'Gini, including students', kind: 'line', group: 'Income after tax, Statistics Norway' },
              { s: 'p90p10', short: 'P90/P10', kind: 'line', group: 'Income after tax, Statistics Norway' }, { s: 's80s20', short: 'S80/S20', kind: 'line', group: 'Income after tax, Statistics Norway' },
              { s: 'wealth_top10', short: 'Top 10 %', kind: 'line', group: 'Share of net wealth, Statistics Norway' }, { s: 'wealth_top5', short: 'Top 5 %', kind: 'line', group: 'Share of net wealth, Statistics Norway' },
              { s: 'wealth_top1', short: 'Top 1 %', kind: 'line', group: 'Share of net wealth, Statistics Norway' }, { s: 'wealth_top01', short: 'Top 0.1 %', kind: 'line', group: 'Share of net wealth, Statistics Norway' },
              { s: 'wealth_bottom50', short: 'Bottom half', kind: 'line', group: 'Share of net wealth, Statistics Norway' },
              { s: 'wid_income_top1', short: 'Income, top 1 %', kind: 'line', group: 'Top shares, World Inequality Database' }, { s: 'wid_income_top10', short: 'Income, top 10 %', kind: 'line', group: 'Top shares, World Inequality Database' },
              { s: 'wid_wealth_top1', short: 'Wealth, top 1 %', kind: 'line', group: 'Top shares, World Inequality Database' }, { s: 'wid_wealth_top10', short: 'Wealth, top 10 %', kind: 'line', group: 'Top shares, World Inequality Database' }],
      defaultYears: 30,
    },
    {
      id: 'consumption', title: 'Consumption', kicker: 'Activity', goodUp: true,
      desc: 'Household consumption and general government consumption from the quarterly national accounts. Volumes at constant 2023 prices, seasonally adjusted. The latest quarters are preliminary.',
      headline: { series: 'households_qoq', fmt: (v) => fmt.signedPct(v), sub: 'household consumption, quarter-on-quarter, s.a.' },
      secondary: [{ series: 'households_yoy', label: 'y/y', fmt: fmt.signedPct }],
      spark: 'households_qoq', sparkZero: true, scale: 1 / 1000, unitLabel: 'NOK bn',
      views: [['qoq', 'Quarter-on-quarter growth', 'bar'], ['yoy', 'Growth from a year earlier', 'line'], ['level', 'Level per quarter, constant prices', 'line']].flatMap(([suffix, group, kind]) =>
        [['households', 'Households'], ['goods', 'Goods'], ['services', 'Services'], ['abroad', 'Purchases abroad'], ['government', 'General government']]
          .map(([k, t]) => ({ s: `${k}_${suffix}`, short: t, tip: `${t}, ${suffix === 'qoq' ? 'q/q' : suffix === 'yoy' ? 'y/y' : 'level'}`, kind, on: k === 'households' && suffix !== 'level', group }))),
      defaultYears: 10,
    },
    {
      id: 'investment', title: 'Investment', kicker: 'Activity', goodUp: true,
      desc: 'Gross fixed capital formation from the quarterly national accounts. Mainland investment excludes petroleum extraction, pipeline transport and ocean shipping, and is split into business, housing and general government investment. Volumes at constant 2023 prices, seasonally adjusted. Quarterly investment figures are volatile and the latest quarters are preliminary.',
      headline: { series: 'mainland_qoq', fmt: (v) => fmt.signedPct(v), sub: 'mainland investment, quarter-on-quarter, s.a.' },
      secondary: [{ series: 'mainland_yoy', label: 'y/y', fmt: fmt.signedPct }],
      spark: 'mainland_yoy', sparkZero: true, scale: 1 / 1000, unitLabel: 'NOK bn',
      views: [['yoy', 'Growth from a year earlier', 'line'], ['qoq', 'Quarter-on-quarter growth', 'bar'], ['level', 'Level per quarter, constant prices', 'line']].flatMap(([suffix, group, kind]) =>
        [['mainland', 'Mainland Norway'], ['business', 'Business'], ['housing', 'Housing'], ['government', 'General government'], ['petroleum', 'Petroleum'], ['total', 'Total']]
          .map(([k, t]) => ({ s: `${k}_${suffix}`, short: t, tip: `${t}, ${suffix === 'qoq' ? 'q/q' : suffix === 'yoy' ? 'y/y' : 'level'}`, kind, on: k === 'mainland' && suffix === 'yoy', group }))),
      defaultYears: 10,
    },
    {
      id: 'trade', title: 'Trade balance', kicker: 'External trade', goodUp: null,
      desc: 'Monthly trade in goods, seasonally adjusted. The total balance is dominated by oil and gas. The mainland balance compares exports excluding oil, gas, ships and platforms with imports excluding ships and platforms.',
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
      headline: { series: 'pct_gdp', fmt: (v) => fmt.pct(v), sub: 'percent of GDP, last four quarters' },
      secondary: [{ series: 'balance', label: 'latest quarter', fmt: fmt.bnFromMn }],
      spark: 'pct_gdp', sparkZero: true,
      views: [{ s: 'pct_gdp', short: '% of GDP', kind: 'line', on: true }, { s: 'balance', short: 'Balance', kind: 'bar' }, { s: 'goods_services', short: 'Goods & services', kind: 'line' }, { s: 'goods', short: 'Goods', kind: 'line' },
              { s: 'services', short: 'Services', kind: 'line' }, { s: 'income', short: 'Income & transfers', kind: 'line' }, { s: 'balance_4q', short: 'Balance, 4-quarter sum', kind: 'line' }],
      scale: 1 / 1000, unitLabel: 'NOK bn', defaultYears: 20,
    },
  ];

  // ------------------------------------------------------------------ comparisons with other countries
  // Two blocks with the same seven tiles: Norway against the Nordic countries, and against Europe and the US.
  // A country keeps one color (slot) across the tiles of a block. A dashed line is a second measure for the same country.
  const BLOCKS = {
    nordic: {
      kicker: 'Nordic comparison', others: 'the other Nordic countries', shown: 5,
      countries: [['no', 'Norway'], ['se', 'Sweden'], ['dk', 'Denmark'], ['fi', 'Finland'], ['is', 'Iceland']],
      policy: [['no', 'Norway', 0], ['se', 'Sweden', 1], ['dk', 'Denmark', 2], ['fi', 'Finland (euro area)', 3], ['is', 'Iceland', 4]],
      yields: [['no', 'Norway', 0], ['se', 'Sweden', 1], ['dk', 'Denmark', 2], ['fi', 'Finland', 3], ['is', 'Iceland', 4]],
      ratesNote: 'Finland is in the euro area and has the European Central Bank’s rate. The OECD’s yield series for Iceland ends in 2022.',
      inflationNote: '', employmentNote: '', wageNote: ' Real wages for Iceland start in 2017, when its harmonized price index starts.',
      tfpNote: 'TFP needs a capital stock, which ends earlier than the other series for some countries and is missing for Iceland.',
    },
    europe: {
      kicker: 'Europe and the US', others: 'the United States, the euro area and its largest economies', shown: 6,
      countries: [['no', 'Norway'], ['us', 'United States'], ['ea', 'Euro area'], ['de', 'Germany'], ['fr', 'France'], ['nl', 'Netherlands'], ['it', 'Italy'], ['es', 'Spain']],
      policy: [['no', 'Norway', 0], ['us', 'United States', 1], ['ea', 'Euro area', 2], ['gb', 'United Kingdom', 5], ['ch', 'Switzerland', 7]],
      yields: [['no', 'Norway', 0], ['us', 'United States', 1], ['de', 'Germany', 3], ['fr', 'France', 4], ['it', 'Italy', 6], ['gb', 'United Kingdom', 5], ['ch', 'Switzerland', 7]],
      ratesNote: 'Germany, France and Italy share the euro area’s policy rate, so they appear only among the bond yields. The United Kingdom and Switzerland appear only in this tile.',
      inflationNote: ' The United States has no current harmonized index, so its figures are the national consumer price index, which includes owner-occupied housing costs. Its core measure excludes food and energy.',
      employmentNote: ' The United States figure comes from the OECD, covers ages 16 to 64 and has no series for ages 20 to 64.',
      wageNote: ' For the United States, wages are measured per full-time equivalent employee and deflated by the national consumer price index.',
      tfpNote: 'TFP needs a capital stock, which ends earlier than the other series for some countries. For the United States, output is real GDP and the capital stock comes from the Bureau of Economic Analysis.',
    },
  };
  function compareDefs(block) {
    const B = BLOCKS[block];
    const C = B.countries.map(([key, label], slot) => ({ key, label, slot, on: slot < B.shown }));
    // GDP and productivity: Norway is Mainland Norway (Statistics Norway), with the total economy as a dashed alternative
    const M = [{ key: 'mainland', label: 'Mainland Norway', slot: 0, on: true }, ...C.slice(1), { key: 'no', label: 'Norway, total economy', slot: 0, dash: true }];
    const P = B.policy.map(([key, label, slot]) => ({ key, label, slot, on: true }));
    const Y = B.yields.map(([key, label, slot]) => ({ key, label, slot }));
    const views = (list, prefix, group, o = {}) => list.map((c) => ({ s: `${prefix}_${c.key}`, short: c.label, tip: o.tip ? `${c.label}, ${o.tip}` : c.label, kind: 'line', slot: c.slot, dash: !!(o.dash || c.dash), on: !!(o.on && c.on), group }));
    const rows = (list, prefix) => list.filter((c) => c.on).map((c) => ({ series: `${prefix}_${c.key}`, label: c.label, slot: c.slot }));
    const mk = (kind, o) => ({ id: `${block}_${kind}`, section: block, kicker: B.kicker, goodUp: null, headline: { series: o.compare.rows[0].series, fmt: o.compare.fmt, sub: o.compare.sub }, ...o });
    return [
      mk('gdp', {
        title: 'GDP',
        desc: `Gross domestic product in Norway and ${B.others}. Volumes, seasonally and calendar adjusted, from Eurostat${block === 'europe' ? ', and from the Bureau of Economic Analysis for the United States' : ''}. Norway is shown as Mainland Norway, from Statistics Norway, because the total economy moves with oil and gas production. The total is available as a dashed line. The index is set to 100 in the fourth quarter of 2019.`,
        compare: { rows: rows(M, 'yoy'), fmt: (v) => fmt.signedPct(v), sub: 'volume growth from a year earlier' }, sparks: rows(M, 'index'),
        views: [...views(M, 'index', 'Index, Q4 2019 = 100', { on: true }), ...views(M, 'yoy', 'Growth from a year earlier', { tip: 'y/y' }), ...views(M, 'qoq', 'Quarter-on-quarter growth', { tip: 'q/q' })],
        defaultYears: 10,
      }),
      mk('inflation', {
        title: 'Inflation',
        desc: 'Twelve-month change in the harmonized index of consumer prices (HICP), which is compiled on the same definitions in every country and published by Eurostat. It differs from the national consumer price index in the inflation tile at the top of the page, mainly because it leaves out owner-occupied housing costs. The headline is core inflation, i.e. the HICP excluding energy, food, alcohol and tobacco. Dashed lines show the HICP for all items.' + B.inflationNote,
        compare: { rows: rows(C, 'core'), fmt: (v) => fmt.pct(v), sub: 'core HICP, 12-month change' }, sparkZero: true,
        views: [...views(C, 'core', 'Core: excluding energy, food, alcohol and tobacco', { on: true, tip: 'core' }), ...views(C, 'hicp', 'HICP, all items', { dash: true, tip: 'all items' })],
        defaultYears: 10,
      }),
      mk('rates', {
        title: 'Interest rates',
        desc: `Central bank policy rates at the end of each month, from the Bank for International Settlements, and yields on ten-year government bonds as dashed lines, from the OECD. ${B.ratesNote}`,
        compare: { rows: rows(P, 'policy'), fmt: (v) => fmt.pct(v, 2), sub: 'central bank policy rate' },
        views: [...views(P, 'policy', 'Policy rate', { on: true, tip: 'policy rate' }), ...views(Y, 'gov10y', '10-year government bond yield', { dash: true, tip: '10-year yield' })],
        defaultYears: 20,
      }),
      mk('unemployment', {
        title: 'Unemployment',
        desc: 'Unemployed persons as a share of the labor force, ages 15 to 74, seasonally adjusted. The figures come from each country’s labor force survey as harmonized by Eurostat. Dashed lines show unemployment among people under 25.',
        compare: { rows: rows(C, 'rate'), fmt: (v) => fmt.pct(v), sub: 'unemployment rate, ages 15–74' },
        views: [...views(C, 'rate', 'Unemployment rate, ages 15–74', { on: true }), ...views(C, 'youth', 'Youth unemployment, under 25', { dash: true, tip: 'under 25' })],
        defaultYears: 10,
      }),
      mk('employment', {
        title: 'Employment',
        desc: 'Employed persons as a share of the population aged 15 to 64, seasonally adjusted. The figures come from each country’s labor force survey as harmonized by Eurostat, and start in 2009 for most countries. Dashed lines show the rate for ages 20 to 64.' + B.employmentNote,
        compare: { rows: rows(C, 'rate1564'), fmt: (v) => fmt.pct(v), sub: 'employment rate, ages 15–64' },
        views: [...views(C, 'rate1564', 'Ages 15–64', { on: true }), ...views(C, 'rate2064', 'Ages 20–64', { dash: true, tip: 'ages 20–64' })],
        defaultYears: 20,
      }),
      mk('wages', {
        title: 'Wages',
        desc: 'Annual wage growth, measured as wages and salaries per hour worked by employees in each country’s national accounts. Dashed lines show real wage growth, i.e. wage growth deflated by the harmonized index of consumer prices. Norway is computed in the same way as the other countries, so the figures differ from annual earnings in the wage tile at the top of the page.' + B.wageNote,
        compare: { rows: rows(C, 'nominal'), fmt: (v) => fmt.pct(v), sub: 'annual wage growth' }, sparkZero: true,
        views: [...views(C, 'nominal', 'Nominal wage growth', { on: true, tip: 'nominal' }), ...views(C, 'real', 'Real wage growth', { dash: true, tip: 'real' })],
        defaultYears: 30, showMethod: true,
        averages: { title: 'Average annual wage growth by country', measures: [{ prefix: 'real', label: 'Real wages', noun: 'Real wage' }, { prefix: 'nominal', label: 'Nominal wages', noun: 'Nominal wage' }], countries: C, years: 10 },
      }),
      mk('productivity', {
        title: 'Productivity',
        desc: 'Total factor productivity and labor productivity (value added per hour worked), computed from each country’s national accounts with the same method as in the productivity tile for Mainland Norway. TFP growth comes from growth accounting, weighting hours and capital by the labor share observed in each country’s national accounts. Norway is shown as Mainland Norway because oil and gas production distorts the total. The total is available as a dashed line. The indices are set to 100 in 2000. ' + B.tfpNote,
        compare: { rows: rows(M, 'tfpg'), fmt: (v) => fmt.signedPct(v), sub: 'total factor productivity growth' }, sparks: rows(M, 'tfp'),
        views: [...views(M, 'tfp', 'Total factor productivity, index 2000 = 100', { on: true, tip: 'TFP' }), ...views(M, 'lp', 'Labor productivity, index 2000 = 100', { tip: 'labor productivity' }),
                ...views(M, 'tfpg', 'TFP growth', { tip: 'TFP growth' }), ...views(M, 'lpg', 'Labor productivity growth', { tip: 'labor productivity growth' }),
                ...views(M, 'share', 'Labor share used as weight', { tip: 'labor share' })],
        defaultYears: 30, showMethod: true,
        averages: { title: 'Average annual growth by country', measures: [{ prefix: 'tfpg', label: 'TFP' }, { prefix: 'lpg', label: 'Labor productivity' }], countries: M, years: 10 },
      }),
    ];
  }
  DEFS.push(...compareDefs('nordic'), ...compareDefs('europe'));
  const HOSTS = { nordic: 'tiles-nordic', europe: 'tiles-europe' };   // def.section -> the grid that holds its tiles

  // ------------------------------------------------------------------ data load
  let DATA = null;
  const tilesEl = document.getElementById('tiles');
  fetch('data/dashboard.json', { cache: 'no-cache' })
    .then((r) => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
    .then((d) => { DATA = d; renderMeta(); renderTiles(); openFromHash(); })
    .catch((e) => { tilesEl.innerHTML = `<p class="d-error">Could not load the data file (${e.message}). If you opened this page from disk, serve it over http. The data is loaded with fetch().</p>`; });

  function renderMeta() {
    const el = document.getElementById('dash-meta');
    const gen = new Date(DATA.generated);
    const parts = [`Data last fetched ${gen.toLocaleDateString('en-US', { day: 'numeric', month: 'short', year: 'numeric' })}`, 'updated automatically every morning'];
    const errs = Object.keys(DATA.errors || {});
    if (errs.length) parts.push(`<span class="warn">last fetch failed for: ${errs.join(', ')} (showing older data)</span>`);
    el.innerHTML = parts.join(' · ');
  }

  // ------------------------------------------------------------------ tiles
  function renderTiles() {
    tilesEl.innerHTML = '';
    Object.values(HOSTS).forEach((id) => { const el = document.getElementById(id); if (el) el.innerHTML = ''; });
    for (const def of DEFS) {
      const ind = DATA.indicators[def.id];
      if (!ind) continue;
      const host = def.section ? document.getElementById(HOSTS[def.section]) : tilesEl;
      const s = ind.series[def.headline.series];
      if (!host || !s) continue;
      const L = last(s), P = last(s, 2);
      const b = document.createElement('button');
      b.className = 'tile' + (ind.stale ? ' stale' : '');
      b.type = 'button';
      b.addEventListener('click', () => openDetail(def.id));
      if (def.compare) {   // comparison tile: one row per country instead of one headline number
        const rows = def.compare.rows.filter((r) => ind.series[r.series]);
        b.setAttribute('aria-label', `${def.title}, ${def.compare.sub}: ${rows.map((r) => `${r.label} ${def.compare.fmt(last(ind.series[r.series]).v)}`).join(', ')}. Open details.`);
        b.innerHTML = `
        <div class="t-head"><div class="t-title">${def.title}</div><div class="t-period">${periodLabel(L.p, s.freq)}</div></div>
        <div class="t-sub">${def.compare.sub}</div>
        <div class="t-rows">${rows.map((r, i) => {
          const rs = ind.series[r.series], l = last(rs);   // a country with another latest period than Norway gets its period written out
          return `<div class="t-row${i === 0 ? ' first' : ''}"><span class="dot" style="background:var(${SLOTS[r.slot]})"></span><span class="c">${r.label}</span>${l.p !== L.p ? `<span class="p">${periodLabel(l.p, rs.freq)}</span>` : ''}<span class="v">${def.compare.fmt(l.v)}</span></div>`;
        }).join('')}</div>
        <div class="t-spark">${sparkMulti(ind, def)}</div>
        <div class="t-foot"><span>${def.kicker}</span><span class="more">Explore →</span></div>`;
        host.appendChild(b);
        const blockEl = host.closest('.dash-block');
        if (blockEl) blockEl.hidden = false;
        continue;
      }
      b.setAttribute('aria-label', `${def.title}: ${def.headline.fmt(L.v)} (${periodLabel(L.p, s.freq)}). Open details.`);
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
      host.appendChild(b);
    }
  }

  // Sparkline for a comparison tile: one line per country in its color, Norway on top and heavier
  function sparkMulti(ind, def) {
    const items = (def.sparks || def.compare.rows).map((r) => ({ ...r, s: ind.series[r.series] })).filter((r) => r.s);
    if (!items.length) return '';
    const f = items[0].s.freq, n = f === 'M' ? 120 : f === 'Q' ? 40 : 25;
    const when = new Map();
    items.forEach((r) => r.s.periods.forEach((p, i) => { if (r.s.values[i] != null) when.set(p, periodTime(p)); }));
    const periods = [...when.keys()].sort((a, b) => when.get(a) - when.get(b)).slice(-n);
    if (periods.length < 2) return '';
    const pos = new Map(periods.map((p, i) => [p, i]));
    const W = 240, H = 56, pad = 3;
    let min = Infinity, max = -Infinity;
    const lines = items.map((r) => {
      const pts = [];
      r.s.periods.forEach((p, i) => { const v = r.s.values[i]; if (v != null && pos.has(p)) { pts.push([pos.get(p), v]); min = Math.min(min, v); max = Math.max(max, v); } });
      return { r, pts };
    });
    if (def.sparkZero) { min = Math.min(min, 0); max = Math.max(max, 0); }
    if (max === min) { max += 1; min -= 1; }
    const x = (i) => pad + (i / (periods.length - 1)) * (W - 2 * pad);
    const y = (v) => pad + (1 - (v - min) / (max - min)) * (H - 2 * pad);
    const zero = def.sparkZero && min < 0 && max > 0 ? `<line class="zero" x1="0" x2="${W}" y1="${y(0).toFixed(1)}" y2="${y(0).toFixed(1)}"/>` : '';
    const path = ({ r, pts }, k) => `<path class="cline${k === 0 ? ' first' : ''}" style="stroke:var(${SLOTS[r.slot]})" d="${pts.map(([i, v], j) => (j ? 'L' : 'M') + x(i).toFixed(1) + ' ' + y(v).toFixed(1)).join('')}"/>`;
    return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true">${zero}${lines.map(path).reverse().join('')}</svg>`;
  }
  function isCurrentYear(p, freq) { return freq === 'A' && +p === new Date().getFullYear(); }

  function deltaHtml(def, s, L, P) {
    if (L.v == null || P.v == null) return '';
    const d = L.v - P.v;
    let txt;
    if (/percent/.test(s.unit)) txt = fmt.pp(d, s.decimals);
    else if (s.unit.startsWith('NOK million')) txt = fmt.signedBnFromMn(d);
    else if (s.unit.startsWith('NOK billion')) txt = fmt.signedBn(d);
    else txt = (d > 0 ? '+' : '') + fmt.num(d, s.decimals);
    const arrow = d > 0 ? '▲' : d < 0 ? '▼' : '■';
    // color only when a direction is unambiguously good or bad for the economy; otherwise stay neutral
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
  let chart = null, chart2 = null, avgChart = null, bdCharts = [], state = null;

  dlg.addEventListener('close', () => { destroyCharts(); if (location.hash) history.replaceState(null, '', location.pathname + location.search); });
  dlg.addEventListener('click', (e) => { if (e.target === dlg) dlg.close(); });
  window.addEventListener('hashchange', openFromHash);
  // #id opens a tile; #id=seriesA,seriesB opens it with those series switched on
  function openFromHash() { const [id, keys] = location.hash.replace('#', '').split('='); if (id && DEFS.some((d) => d.id === id) && DATA) openDetail(id, true, keys ? keys.split(',') : null); }
  function destroyCharts() { if (chart) { chart.destroy(); chart = null; } if (chart2) { chart2.destroy(); chart2 = null; } if (avgChart) { avgChart.destroy(); avgChart = null; } bdCharts.forEach((c) => c.destroy()); bdCharts = []; }

  function openDetail(id, fromHash, keys) {
    const def = DEFS.find((d) => d.id === id), ind = DATA.indicators[id];
    if (!def || !ind) return;
    destroyCharts();
    if (!fromHash) history.replaceState(null, '', '#' + id);
    const wanted = (keys || []).filter((k) => ind.series[k] && def.views.some((v) => v.s === k));
    const on = new Set(wanted.length ? wanted : def.views.filter((v) => v.on && ind.series[v.s]).map((v) => v.s));
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
    if (def.averages) initAverages();
    if (ind.breakdowns) drawBreakdowns(ind.breakdowns);
    if (ind.scatters) drawScatters(ind.scatters);
    dlg.scrollTop = 0;
  }

  // Series of different frequencies cannot share one category axis: switching one on drops the others
  function toggleSeries(key) {
    const { freq, unit } = state.ind.series[key];
    if (state.on.has(key)) { if (state.on.size > 1) state.on.delete(key); }
    else {   // one axis: switching a series on drops those with another frequency or another unit
      for (const k of [...state.on]) { const o = state.ind.series[k]; if (o.freq !== freq || o.unit !== unit) state.on.delete(k); }
      state.on.add(key);
    }
    syncPills(); drawChart();
  }

  const viewOf = (key) => state.def.views.find((v) => v.s === key);
  const viewLabel = (v, s) => v.short || (s ? s.label.split(/[,(]/)[0].trim() : v.s);
  // Color slot of a view; a projection (tint) borrows the color of the series it extends
  // A view can also name its color itself (slot), which keeps a country in one color across groups and tiles
  const slotOf = (def, v) => { const b = (v.tint && def.views.find((x) => x.s === v.tint)) || v; return (b.slot != null ? b.slot : def.views.indexOf(b)) % 8; };
  // The palette has eight colors. Series that are switched on never share one: a clash moves the later series to the next free color.
  // A dashed series (dash) may share its color with a solid one: it is another measure for the same country.
  function assignSlots() {
    const used = new Set(), map = new Map();
    const active = state.def.views.filter((v) => state.on.has(v.s));
    for (const v of active.filter((x) => !x.tint)) {
      const kind = v.dash ? 'd' : 's';
      let slot = slotOf(state.def, v), n = 0;
      while (used.has(kind + slot) && n < 8) { slot = (slot + 1) % 8; n++; }
      used.add(kind + slot); map.set(v.s, slot);
    }
    for (const v of active.filter((x) => x.tint)) map.set(v.s, map.has(v.tint) ? map.get(v.tint) : slotOf(state.def, v));
    state.slots = map;
  }
  const slotFor = (def, v) => (state && state.def === def && state.slots && state.slots.has(v.s) ? state.slots.get(v.s) : slotOf(def, v));
  const swatchStyle = (def, v, s) => `background:${(s && s.projection) || v.dash ?`repeating-linear-gradient(90deg, var(${SLOTS[slotFor(def, v)]}) 0 4px, transparent 4px 7px)` : `var(${SLOTS[slotFor(def, v)]})`}`;

  function buildDetailHtml(def, ind) {
    const hs = ind.series[def.headline.series];
    const L = last(hs), P = last(hs, 2);
    const stats = def.compare ? def.compare.rows.filter((r) => ind.series[r.series]).map((r) => { const ss = ind.series[r.series], l = last(ss); return { k: r.label, v: def.compare.fmt(l.v), s: periodLabel(l.p, ss.freq) }; }) : [
      { k: 'Latest', v: def.headline.fmt(L.v), s: periodLabel(L.p, hs.freq) },
      { k: 'Previous', v: def.headline.fmt(P.v), s: periodLabel(P.p, hs.freq) },
      ...(def.secondary || []).map((x) => { const ss = ind.series[x.series]; if (!ss) return null; const l = last(ss); return { k: x.label, v: x.fmt(l.v), s: periodLabel(l.p, ss.freq) }; }).filter(Boolean),
    ];
    const ranges = def.defaultYears >= 30 ? [10, 20, 30, 100] : [5, 10, 20, 100];
    const pill = (v) => `<button class="pill" type="button" data-series="${v.s}" aria-pressed="false" title="${ind.series[v.s].label}"><span class="sw" style="${swatchStyle(def, v, ind.series[v.s])}"></span>${viewLabel(v, ind.series[v.s])}</button>`;
    const avail = def.views.filter((v) => ind.series[v.s]);
    const groups = [...new Set(avail.map((v) => v.group || 'Series'))];
    const dense = groups.length > 3 || avail.length > 16;   // many series: fold each group, open only those with a series switched on
    const pillRows = groups.map((g) => {
      const vs = avail.filter((v) => (v.group || 'Series') === g);
      if (!dense) return `<div class="group"><span class="label">${g}</span>${vs.map(pill).join('')}</div>`;
      return `<details class="fold"${vs.some((v) => state.on.has(v.s)) ? ' open' : ''}><summary><span class="label">${g}</span><span class="count">${vs.length} series</span></summary><div class="group">${vs.map(pill).join('')}</div></details>`;
    }).join('');
    const sources = (ind.sources || []).map((s) => `<li>${s.url ? `<a href="${s.url}">${s.name}</a>` : s.name}${s.table ? ` (${/^\d/.test(s.table) ? 'table ' : 'dataflow '}${s.table})` : ''}${s.updated ? `, updated ${s.updated}` : ''}${s.note ? `. ${s.note}` : ''}</li>`).join('');
    const derivedAll = Object.values(ind.series).filter((s) => s.derived);
    // A tile where every series is an own calculation says so in one line instead of listing them all
    const derived = derivedAll.length > 8 && derivedAll.length === Object.keys(ind.series).length
      ? '<li>Every series in this tile is my own calculation from the sources below.</li>'
      : derivedAll.map((s) => `<li>${s.label}${s.note ? ': ' + s.note : ''}</li>`).join('');
    return `
      <div class="d-top">
        <div><div class="kicker">${def.kicker}</div><h2 id="d-title">${def.title}</h2></div>
        <button class="d-close" type="button" aria-label="Close">✕</button>
      </div>
      <p class="d-desc">${def.desc}</p>
      ${def.compare ? `<p class="d-statlabel">Latest reading, ${def.compare.sub}</p>` : ''}
      <div class="d-stats">${stats.map((s) => `<div class="st"><div class="k">${s.k}</div><div class="v">${s.v}<small>${s.s}</small></div></div>`).join('')}</div>
      <div class="d-controls">
        <div class="group"><span class="label">Range</span>${ranges.map((y) => `<button class="pill" type="button" data-years="${y}">${y >= 100 ? 'Max' : y + ' y'}</button>`).join('')}</div>
        ${pillRows}
        <div class="group"><button class="pill" type="button" data-table aria-pressed="false">Table</button><button class="pill" type="button" data-csv>Download CSV</button></div>
      </div>
      <div class="d-chart"><canvas id="d-canvas" role="img" aria-label="${def.title} chart"></canvas></div>
      <div class="d-legend" id="d-legend"></div>
      <div class="d-table-wrap" id="d-table" hidden></div>
      ${def.averages ? `<div class="d-second">
        <h3>${def.averages.title}</h3>
        <div class="d-controls">
          <div class="group"><span class="label">Measure</span>${def.averages.measures.map((m) => `<button class="pill" type="button" data-avg-measure="${m.prefix}" aria-pressed="false">${m.label}</button>`).join('')}</div>
          <div class="group"><span class="label">Period</span><label class="yr">From <select id="d-avg-from" aria-label="First year"></select></label><label class="yr">to <select id="d-avg-to" aria-label="Last year"></select></label>
            ${[5, 10, 20, 0].map((n) => `<button class="pill" type="button" data-avg-last="${n}" aria-pressed="false">${n ? `Last ${n} y` : 'All years'}</button>`).join('')}</div>
        </div>
        <p class="d-sub" id="d-avg-sub"></p>
        <div class="d-chart" id="d-avg-box"><canvas id="d-avg-canvas" role="img" aria-label="${def.averages.title}"></canvas></div>
      </div>` : ''}
      ${def.second ? `<div class="d-second"><h3>${def.second.title}</h3><div class="d-chart"><canvas id="d-canvas2" role="img" aria-label="${def.second.title}"></canvas></div></div>` : ''}
      ${(ind.breakdowns || []).map((b, i) => `<div class="d-second"><h3>${b.title}</h3><p class="d-sub">${b.subtitle}</p><div class="d-chart" style="height:${b.items.length * (window.innerWidth < 700 ? 40 : 28) + 60}px"><canvas id="d-bd-${i}" role="img" aria-label="${b.title}"></canvas></div></div>`).join('')}
      ${(ind.scatters || []).map((b, i) => `<div class="d-second"><h3>${b.title}</h3><p class="d-sub">${b.subtitle}</p><div class="d-chart" style="height:440px"><canvas id="d-sc-${i}" role="img" aria-label="${b.title}"></canvas></div></div>`).join('')}
      ${def.decisions && ind.decisions ? decisionsHtml(ind.decisions) : ''}
      <div class="d-notes">
        ${def.showMethod && ind.method ? `<h4>Method</h4><p class="method">${ind.method}</p>` : ''}
        ${derived ? `<h4>Derived series (own calculations, marked * in legends)</h4><ul>${derived}</ul>` : ''}
        <h4>Sources</h4><ul>${sources}</ul>
        ${ind.fetched ? `<p class="fetched">Data fetched ${new Date(ind.fetched).toLocaleDateString('en-US', { day: 'numeric', month: 'short', year: 'numeric' })}.</p>` : ''}
        ${ind.stale ? `<p class="warn">The last automatic update of this indicator failed (${ind.error}); figures are from the previous successful fetch.</p>` : ''}
      </div>`;
  }

  function decisionsHtml(dec) {
    const rows = dec.slice(-12).reverse().map((d) => `<tr><td>${periodLabel(d.date, 'D')}</td><td>${fmt.num(d.rate, 2)} %</td><td class="chg ${d.change > 0 ? 'up' : d.change < 0 ? 'down' : ''}">${d.change > 0 ? '+' : ''}${fmt.num(d.change, 2)}</td></tr>`).join('');
    return `<div class="d-notes"><h4>Recent policy rate decisions (announcement date, new rate, change)</h4><table class="decisions"><tbody>${rows}</tbody></table></div>`;
  }

  function syncPills() {
    inner.querySelectorAll('[data-years]').forEach((b) => b.setAttribute('aria-pressed', String(+b.dataset.years === state.years)));
    assignSlots();
    inner.querySelectorAll('[data-series]').forEach((b) => {
      b.setAttribute('aria-pressed', String(state.on.has(b.dataset.series)));
      const v = viewOf(b.dataset.series);
      b.querySelector('.sw').style.cssText = swatchStyle(state.def, v, state.ind.series[v.s]);
    });
    inner.querySelector('[data-table]').setAttribute('aria-pressed', String(state.table));
  }

  // Aligned period axis for the selected series within the chosen range
  function alignedData() {
    const views = state.def.views.filter((v) => state.on.has(v.s));
    const sers = views.map((v) => state.ind.series[v.s]);
    const actual = sers.filter((s) => !s.projection);   // the range is anchored on the data, projections extend beyond it
    const endT = Math.max(...(actual.length ? actual : sers).map((s) => periodTime(s.periods[s.periods.length - 1])));
    const startT = state.years >= 100 ? -Infinity : endT - state.years + 1 / 12;
    const periodSet = new Map();
    sers.forEach((s) => s.periods.forEach((p) => { const t = periodTime(p); if (t >= startT) periodSet.set(p, t); }));
    let periods = [...periodSet.keys()].sort((a, b) => periodSet.get(a) - periodSet.get(b));
    // Quarterly projections sit on a monthly category axis: fill in the months in between so time stays proportional
    if (sers.some((s) => s.projection) && sers.every((s) => s.freq === 'M') && periods.length) {
      const [y0, m0] = periods[0].split('-').map(Number), [y1, m1] = periods[periods.length - 1].split('-').map(Number);
      periods = [];
      for (let y = y0, m = m0; y < y1 || (y === y1 && m <= m1); m === 12 ? (y++, m = 1) : m++) periods.push(`${y}-${String(m).padStart(2, '0')}`);
    }
    if (sers.every((s) => s.freq === 'A') && periods.length > 1) {
      const y0 = +periods[0], y1 = +periods[periods.length - 1];
      if (y1 - y0 + 1 > periods.length) { periods = []; for (let y = y0; y <= y1; y++) periods.push(String(y)); }
    }
    const datasets = views.map((v) => {
      const s = state.ind.series[v.s];
      const map = new Map(s.periods.map((p, i) => [p, s.values[i]]));
      const scale = (state.def.scale && s.unit.startsWith('NOK million')) ? state.def.scale : 1;
      return { v, s, slot: slotFor(state.def, v), scale, label: v.tip || viewLabel(v, s),
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
    const [y, m] = p.split('-'); return MONTHS[+m - 1] + ' ' + y.slice(2);   // daily series: month and year are enough on the axis
  };
  const unitShort = (u) => (!u || u === 'index' ? '' : u.replace('percent of GDP', '% of GDP').replace('percent of labor force', '%').replace('percent of population', '%').replace('percent', '%'));

  function baseOptions(periods, unit, extraPlugins) {
    const ink = cssVar('--viz-ink-2'), grid = cssVar('--viz-grid');
    const tickEvery = Math.max(1, Math.round(periods.length / 9));
    return {
      responsive: true, maintainAspectRatio: false, animation: false, normalized: true,
      interaction: { mode: 'index', intersect: false },
      layout: { padding: { top: 6, right: 18 } },
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
             ticks: { color: ink, font: { size: 11 }, callback(v, i, ticks) {   // as many decimals as the spacing between the ticks needs
               const step = ticks.length > 1 ? Math.abs(ticks[1].value - ticks[0].value) : 1;
               return fmt.num(v, step >= 1 ? 0 : step >= 0.1 ? 1 : step >= 0.01 ? 2 : 3);
             } },
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
      const proj = d.s.projection ? { borderDash: [6, 4], spanGaps: true } : d.v.dash ? { borderDash: [5, 3] } : {};
      return { ...base, type: 'line', borderColor: color, backgroundColor: color, borderWidth: 2, pointRadius: 0, pointHoverRadius: 4, pointHitRadius: 10, spanGaps: periods.length < 400, tension: 0.15, order: 1, ...proj };
    });
    // Optional (def.avgYears): a dashed line at each series' average over its last N observations, drawn over that window only
    const avgs = [];
    if (state.def.avgYears) {
      datasets.forEach((d) => {
        if (d.v.kind !== 'line' || d.s.projection) return;
        const obs = d.s.periods.map((p, i) => [p, d.s.values[i]]).filter(([, v]) => v != null).slice(-state.def.avgYears);
        if (obs.length < 2) return;
        // avgFrom: the line is drawn at another series' average (the common component is centered on the manufacturing share's average)
        const ref = d.v.avgFrom && state.ind.series[d.v.avgFrom];
        const src = ref ? ref.periods.map((p, i) => [p, ref.values[i]]).filter(([, v]) => v != null).slice(-state.def.avgYears) : obs;
        const mean = (src.reduce((a, [, v]) => a + v, 0) / src.length) * d.scale;
        const win = new Set(obs.map(([p]) => p));
        const color = cssVar(SLOTS[d.slot % 8]);
        ds.push({ label: `${d.label}, average ${obs[0][0]}–${obs[obs.length - 1][0]}`, data: periods.map((p) => (win.has(p) ? mean : null)), _avg: true, _dec: d.s.decimals, _unit: unitShort(d.unit),
                  type: 'line', borderColor: color, backgroundColor: color, borderWidth: 1.5, borderDash: [5, 4], pointRadius: 0, pointHoverRadius: 4, pointHitRadius: 10, spanGaps: true, order: 0 });
        avgs.push({ d, mean, from: obs[0][0], to: obs[obs.length - 1][0] });
      });
    }
    const inflationKeys = ['cpi_yoy', 'cpiate_yoy', 'cpiat_yoy', 'proj_cpi', 'proj_cpiate'];
    const showTarget = state.def.target && datasets.some((d) => inflationKeys.includes(d.v.s)) && datasets.every((d) => inflationKeys.includes(d.v.s));
    const opts = baseOptions(periods, unit, { crosshair: {}, targetLine: showTarget ? { value: state.def.target, label: 'Target 2 %' } : {} });
    const emphasizeZero = datasets.some((d) => d.v.kind === 'bar' || d.data.some((v) => v != null && v < 0));
    if (emphasizeZero) opts.scales.y.grid.color = (ctx) => (ctx.tick.value === 0 ? cssVar('--viz-axis') : cssVar('--viz-grid'));
    const ctx = document.getElementById('d-canvas').getContext('2d');
    if (chart) chart.destroy();
    chart = new Chart(ctx, { type: 'line', data: { labels: periods, datasets: ds }, options: opts, plugins: [crosshair, targetLine] });
    document.getElementById('d-legend').innerHTML = datasets.map((d) =>
      `<span><span class="sw ${d.v.kind}" style="${swatchStyle(state.def, d.v, d.s)}"></span>${d.s.label}${d.s.derived ? ' *' : ''}</span>`).join('')
      + avgs.map(({ d, mean, from, to }) =>
        `<span><span class="sw line" style="background:repeating-linear-gradient(90deg, var(${SLOTS[d.slot % 8]}) 0 4px, transparent 4px 7px)"></span>${d.label}: average ${from}–${to}, ${fmt.num(mean, d.s.decimals)}${unitShort(d.unit) ? ' ' + unitShort(d.unit) : ''}</span>`).join('');
    if (state.table) drawTable();
  }

  // Average annual growth per country over a period the reader chooses (def.averages): one bar per country, in the country's color.
  // The average is a compound rate, i.e. the mean of the log changes. A country without figures for the whole period is
  // averaged over the years it has, and those years are written next to its name.
  function avgYearSpan() {
    const ys = [];
    state.def.averages.countries.forEach((c) => { const s = state.ind.series[`${state.avg.measure}_${c.key}`]; if (s) s.periods.forEach((p, i) => { if (s.values[i] != null) ys.push(+p); }); });
    return [Math.min(...ys), Math.max(...ys)];
  }
  function setAvgPeriod(last) {   // last: number of years ending in the latest year, 0 for all years, null to keep the choice within the available span
    const [lo, hi] = avgYearSpan(), a = state.avg;
    if (last != null) { a.to = hi; a.from = last ? Math.max(lo, hi - last + 1) : lo; }
    a.from = Math.min(Math.max(a.from, lo), hi); a.to = Math.min(Math.max(a.to, lo), hi);
    if (a.from > a.to) [a.from, a.to] = [a.to, a.from];
  }
  function initAverages() {
    const A = state.def.averages;
    state.avg = { measure: A.measures[0].prefix, from: 0, to: 0 };
    setAvgPeriod(A.years);
    inner.querySelectorAll('[data-avg-measure]').forEach((b) => b.addEventListener('click', () => { state.avg.measure = b.dataset.avgMeasure; setAvgPeriod(null); drawAverages(); }));
    inner.querySelectorAll('[data-avg-last]').forEach((b) => b.addEventListener('click', () => { setAvgPeriod(+b.dataset.avgLast); drawAverages(); }));
    ['from', 'to'].forEach((k) => document.getElementById('d-avg-' + k).addEventListener('change', (e) => { state.avg[k] = +e.target.value; setAvgPeriod(null); drawAverages(); }));
    drawAverages();
  }
  function drawAverages() {
    const A = state.def.averages, a = state.avg, [lo, hi] = avgYearSpan();
    const measure = A.measures.find((m) => m.prefix === a.measure), what = measure.noun || measure.label;
    const years = Array.from({ length: hi - lo + 1 }, (_, i) => lo + i);
    ['from', 'to'].forEach((k) => { document.getElementById('d-avg-' + k).innerHTML = years.map((y) => `<option value="${y}"${y === a[k] ? ' selected' : ''}>${y}</option>`).join(''); });
    inner.querySelectorAll('[data-avg-measure]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.avgMeasure === a.measure)));
    inner.querySelectorAll('[data-avg-last]').forEach((b) => { const n = +b.dataset.avgLast; b.setAttribute('aria-pressed', String(a.to === hi && a.from === (n ? Math.max(lo, hi - n + 1) : lo))); });
    const missing = [];
    const rows = A.countries.map((c) => {
      const s = state.ind.series[`${a.measure}_${c.key}`];
      const obs = s ? s.periods.map((p, i) => [+p, s.values[i]]).filter(([y, v]) => v != null && y >= a.from && y <= a.to) : [];
      if (!obs.length) { missing.push(c.label); return null; }
      const mean = (Math.exp(obs.reduce((sum, [, v]) => sum + Math.log(1 + v / 100), 0) / obs.length) - 1) * 100;
      const y0 = obs[0][0], y1 = obs[obs.length - 1][0];
      return { c, mean, n: obs.length, label: c.label + (y0 !== a.from || y1 !== a.to ? ` (${y0 === y1 ? y0 : `${y0}–${y1}`})` : '') };
    }).filter(Boolean).sort((x, y) => y.mean - x.mean);
    document.getElementById('d-avg-sub').textContent = `${what} growth per year, averaged over ${a.from === a.to ? a.from : `${a.from} to ${a.to}`} as a compound rate. A country without figures for the whole period is averaged over the years in parentheses.`
      + (missing.length ? ` No figures in this period for ${missing.join(' and ')}.` : '');
    const ink = cssVar('--viz-ink-2'), grid = cssVar('--viz-grid'), narrow = window.innerWidth < 700;
    const fill = (c) => { const hex = cssVar(SLOTS[c.slot]).replace('#', ''); return c.dash && hex.length === 6 ? `rgba(${[0, 2, 4].map((k) => parseInt(hex.slice(k, k + 2), 16)).join(', ')}, 0.45)` : '#' + hex; };
    const vals = rows.map((r) => r.mean), top = Math.max(0, ...vals), bottom = Math.min(0, ...vals), pad = Math.max(top - bottom, 0.2) * 0.14;
    const endLabels = {
      id: 'avgLabels',
      afterDatasetsDraw(c) {
        const ctx = c.ctx;
        ctx.save(); ctx.fillStyle = cssVar('--viz-ink'); ctx.font = `11px ${Chart.defaults.font.family}`; ctx.textBaseline = 'middle';
        c.getDatasetMeta(0).data.forEach((bar, i) => { const v = rows[i].mean; ctx.textAlign = v < 0 ? 'right' : 'left'; ctx.fillText(`${v > 0 ? '+' : ''}${fmt.num(v, 2)} %`, bar.x + (v < 0 ? -6 : 6), bar.y); });
        ctx.restore();
      },
    };
    document.getElementById('d-avg-box').style.height = `${rows.length * (narrow ? 38 : 30) + 64}px`;
    if (avgChart) avgChart.destroy();
    avgChart = new Chart(document.getElementById('d-avg-canvas').getContext('2d'), {
      type: 'bar',
      data: { labels: rows.map((r) => r.label), datasets: [{ data: vals, backgroundColor: rows.map((r) => fill(r.c)), borderRadius: 3, borderSkipped: 'start', barPercentage: 0.72 }] },
      options: {
        indexAxis: 'y', responsive: true, maintainAspectRatio: false, animation: false,
        plugins: { legend: { display: false },
                   tooltip: { callbacks: { title: (items) => rows[items[0].dataIndex].label,
                                           label: (it) => ` ${what} growth: ${fmt.num(rows[it.dataIndex].mean, 2)} % per year, average of ${rows[it.dataIndex].n} year${rows[it.dataIndex].n === 1 ? '' : 's'}` } } },
        scales: { x: { suggestedMin: bottom - (bottom < 0 ? pad : 0), suggestedMax: top + pad, grid: { color: (c) => (c.tick.value === 0 ? cssVar('--viz-axis') : grid) }, ticks: { color: ink, font: { size: 11 } }, border: { display: false },
                       title: { display: true, text: 'percent per year', color: ink, font: { size: 11 } } },
                  y: { grid: { display: false }, ticks: { color: ink, font: { size: 11 } }, border: { color: cssVar('--viz-axis') } } },
      },
      plugins: [endLabels],
    });
  }

  // Horizontal bar charts for one-year breakdowns (ind.breakdowns): items sorted by size, value written at the end of each bar
  function drawBreakdowns(list) {
    const ink = cssVar('--viz-ink-2'), grid = cssVar('--viz-grid'), narrow = window.innerWidth < 700;
    const wrap = (text, n) => { const out = ['']; text.split(' ').forEach((w) => { if ((out[out.length - 1] + ' ' + w).trim().length > n) out.push(w); else out[out.length - 1] = (out[out.length - 1] + ' ' + w).trim(); }); return out; };
    const endLabels = {
      id: 'endLabels',
      afterDatasetsDraw(c) {
        const ctx = c.ctx;
        ctx.save(); ctx.fillStyle = cssVar('--viz-ink'); ctx.font = `11px ${Chart.defaults.font.family}`; ctx.textBaseline = 'middle';
        c.getDatasetMeta(0).data.forEach((bar, i) => ctx.fillText(fmt.num(c.data.datasets[0].data[i], 1), bar.x + 6, bar.y));
        ctx.restore();
      },
    };
    list.forEach((b, i) => {
      const el = document.getElementById('d-bd-' + i);
      if (!el) return;
      const max = Math.max(...b.items.map((x) => x.value));
      bdCharts.push(new Chart(el.getContext('2d'), {
        type: 'bar',
        data: { labels: b.items.map((x) => (narrow ? wrap(x.label, 24) : x.label)), datasets: [{ label: b.unit, data: b.items.map((x) => x.value), backgroundColor: cssVar('--s1'), borderRadius: 3, borderSkipped: 'start', barPercentage: 0.72 }] },
        options: {
          indexAxis: 'y', responsive: true, maintainAspectRatio: false, animation: false,
          plugins: { legend: { display: false },
                     tooltip: { callbacks: { title: (items) => b.items[items[0].dataIndex].label,
                                             label: (it) => { const x = b.items[it.dataIndex]; return ` NOK ${fmt.num(x.value, 1)} bn, ${fmt.num((100 * x.value) / b.total, 1)} % of the total${x.group ? ' (' + x.group + ')' : ''}`; } } } },
          scales: { x: { suggestedMax: max * 1.14, grid: { color: grid }, ticks: { color: ink }, border: { display: false }, title: { display: true, text: b.unit, color: ink, font: { size: 11 } } },
                    y: { grid: { display: false }, ticks: { color: ink, font: { size: 11 } }, border: { color: cssVar('--viz-axis') } } },
        },
        plugins: [endLabels],
      }));
    });
  }

  // Connected scatter (ind.scatters): one point per period, joined in time order, later points darker.
  // The first quarter of every third year and the latest point are labeled.
  function drawScatters(list) {
    const ink = cssVar('--viz-ink-2'), grid = cssVar('--viz-grid');
    const hex = cssVar('--s1').replace('#', '');
    const rgb = hex.length === 6 ? [0, 2, 4].map((k) => parseInt(hex.slice(k, k + 2), 16)) : [42, 120, 214];
    const shade = (t) => `rgba(${rgb[0]}, ${rgb[1]}, ${rgb[2]}, ${(0.18 + 0.82 * t).toFixed(3)})`;
    list.forEach((b, i) => {
      const el = document.getElementById('d-sc-' + i);
      if (!el || !b.points.length) return;
      const n = b.points.length, lastIdx = n - 1;
      const labeled = (k) => k === lastIdx || (b.points[k].p.endsWith('Q1') && (+b.points[k].p.slice(0, 4) - +b.points[0].p.slice(0, 4)) % 3 === 0);
      const pointLabels = {
        id: 'pointLabels',
        afterDatasetsDraw(c) {
          const ctx = c.ctx;
          ctx.save(); ctx.font = `11px ${Chart.defaults.font.family}`; ctx.fillStyle = cssVar('--viz-ink'); ctx.textBaseline = 'bottom';
          c.getDatasetMeta(0).data.forEach((pt, k) => { if (labeled(k)) ctx.fillText(k === lastIdx ? periodLabel(b.points[k].p, 'Q') : b.points[k].p.slice(0, 4), pt.x + 6, pt.y - 4); });
          ctx.restore();
        },
      };
      bdCharts.push(new Chart(el.getContext('2d'), {
        type: 'scatter',
        data: { datasets: [{ data: b.points.map((q) => ({ x: q.x, y: q.y })), showLine: true, borderColor: cssVar('--viz-axis'), borderWidth: 1,
                             pointBackgroundColor: b.points.map((q, k) => shade(n > 1 ? k / (n - 1) : 1)), pointBorderColor: b.points.map((q, k) => (k === lastIdx ? cssVar('--viz-ink') : 'transparent')),
                             pointBorderWidth: b.points.map((q, k) => (k === lastIdx ? 2 : 0)), pointRadius: b.points.map((q, k) => (k === lastIdx ? 6 : 3.5)), pointHoverRadius: 6 }] },
        options: {
          responsive: true, maintainAspectRatio: false, animation: false, layout: { padding: { top: 14, right: 40 } },
          plugins: { legend: { display: false },
                     tooltip: { callbacks: { title: (items) => periodLabel(b.points[items[0].dataIndex].p, 'Q'),
                                             label: (it) => ` unemployment ${fmt.num(it.parsed.x, 1)} %, vacancy rate ${fmt.num(it.parsed.y, 1)} %` } } },
          scales: { x: { grid: { color: grid }, ticks: { color: ink, font: { size: 11 } }, border: { color: cssVar('--viz-axis') }, title: { display: true, text: b.x, color: ink, font: { size: 11 } } },
                    y: { grid: { color: grid }, ticks: { color: ink, font: { size: 11 } }, border: { display: false }, title: { display: true, text: b.y, color: ink, font: { size: 11 } } } },
        },
        plugins: [pointLabels],
      }));
    });
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
