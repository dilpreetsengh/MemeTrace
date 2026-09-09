const state = {
  data: null,
  candidates: [],
  filter: 'all',
  selectedMint: null,
  researchFilters: null,
};

const statusLabels = {
  candidate: 'Candidate',
  watch: 'Watch',
  avoid: 'Avoid',
};

const elements = {
  metrics: document.querySelector('#metrics'),
  filters: document.querySelector('#filters'),
  list: document.querySelector('#candidate-list'),
  detail: document.querySelector('#candidate-detail'),
  dataStatus: document.querySelector('#data-status'),
  fullScan: document.querySelector('#full-scan'),
  addressInput: document.querySelector('#token-address'),
  addressSearch: document.querySelector('#address-search'),
  refresh: document.querySelector('#refresh'),
  quoteCheck: document.querySelector('#quote-check'),
  safetyCheck: document.querySelector('#safety-check'),
  walletCheck: document.querySelector('#wallet-check'),
  crosscheck: document.querySelector('#crosscheck'),
  note: document.querySelector('#feed-note'),
  scanSummary: document.querySelector('#scan-summary'),
  filterPanel: document.querySelector('#research-filter-panel'),
  filterStatus: document.querySelector('#filter-status'),
  researchFilterForm: document.querySelector('#research-filter-form'),
  researchFilterFields: document.querySelector('#research-filter-fields'),
  saveResearchFilters: document.querySelector('#save-research-filters'),
  resetResearchFilters: document.querySelector('#reset-research-filters'),
};

const researchFilterGroups = [
  {
    title: 'Discovery: find coins in motion',
    description: 'These decide which current pairs enter the expensive research checks.',
    filters: [
      { key: 'market_cap', label: 'Market cap', fields: [['min', 'Min $'], ['max', 'Max $']] },
      { key: 'liquidity', label: 'Liquidity', fields: [['min', 'Min $']] },
      { key: 'age', label: 'Trading age', fields: [['min', 'Min minutes'], ['max', 'Max minutes']] },
      { key: 'volume_5m', label: '5-minute volume', fields: [['min', 'Min $']] },
      { key: 'swaps_5m', label: '5-minute swaps', fields: [['min', 'Min trades']] },
      { key: 'buy_sell_ratio', label: 'Buy / sell pressure', fields: [['min', 'Min ratio']] },
      { key: 'price_change_5m', label: '5-minute price move', fields: [['min', 'Min %'], ['max', 'Max %']] },
    ],
  },
  {
    title: 'Candidate rating and exit quality',
    description: 'These decide whether a discovered card can graduate from Watch to Candidate.',
    filters: [
      { key: 'candidate_market_cap', label: 'Candidate market-cap lane', fields: [['min', 'Min $'], ['max', 'Max $']] },
      { key: 'candidate_liquidity', label: 'Candidate liquidity', fields: [['min', 'Min $']] },
      { key: 'liquidity_ratio', label: 'Liquidity / market cap', fields: [['min_pct', 'Candidate min %'], ['hard_min_pct', 'Hard min %']] },
      { key: 'volume_to_liquidity', label: '5m volume / liquidity', fields: [['min', 'Min ratio'], ['max', 'Candidate max'], ['hard_max', 'Hard max']] },
      { key: 'sell_impact', label: 'Jupiter sell impact', fields: [['max_pct', 'Candidate max %'], ['hard_max_pct', 'Hard max %']] },
      { key: 'require_sell_route', label: 'Require Jupiter sell route', fields: [] },
      { key: 'require_market_crosscheck', label: 'Require CoinGecko cross-check', fields: [] },
    ],
  },
  {
    title: 'Holder, creator, and authority risk',
    description: 'These use public Solana Tracker and Helius evidence. Provider danger flags remain visible even if you relax a limit.',
    filters: [
      { key: 'tracker_risk_score', label: 'Tracker risk score', fields: [['max', 'Candidate max'], ['hard_max', 'Hard max']] },
      { key: 'top10_holders', label: 'Top 10 holder concentration', fields: [['max_pct', 'Max %']] },
      { key: 'snipers', label: 'Early sniper holdings', fields: [['max_pct', 'Candidate max %'], ['hard_max_pct', 'Hard max %']] },
      { key: 'insiders', label: 'Possible insider holdings', fields: [['max_pct', 'Candidate max %'], ['hard_max_pct', 'Hard max %']] },
      { key: 'bundlers', label: 'Bundled-wallet holdings', fields: [['max_pct', 'Candidate max %'], ['hard_max_pct', 'Hard max %']] },
      { key: 'developer_holdings', label: 'Developer holdings', fields: [['max_pct', 'Candidate max %'], ['hard_max_pct', 'Hard max %']] },
      { key: 'active_authority', label: 'Reject active mint / freeze authority', fields: [] },
      { key: 'creator_activity', label: 'Require clear creator activity evidence', fields: [] },
    ],
  },
];

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (character) => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#039;',
  })[character]);
}

function formatUsd(value) {
  if (value === null || value === undefined) return '—';
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 0,
  }).format(value);
}

function formatCompactUsd(value) {
  if (value >= 1_000_000) return '$' + (value / 1_000_000).toFixed(value >= 10_000_000 ? 0 : 1) + 'm';
  if (value >= 1_000) return '$' + (value / 1_000).toFixed(value >= 100_000 ? 0 : 1) + 'k';
  return formatUsd(value);
}

function formatPercent(value, decimals = 1) {
  if (value === null || value === undefined) return '—';
  return Number(value).toFixed(decimals) + '%';
}

function formatSetup(value) {
  return value === 'panic_reclaim' ? 'Panic reclaim' : 'Early momentum';
}

function shortAddress(value) {
  if (!value) return 'Not returned';
  return String(value).length > 14 ? String(value).slice(0, 6) + '…' + String(value).slice(-5) : String(value);
}

function displayFilterNumber(value) {
  const number = Number(value);
  return Number.isInteger(number) ? String(number) : String(number);
}

function renderResearchFilters() {
  const filters = state.researchFilters;
  if (!filters) return;
  const activeCount = Object.values(filters).filter((filter) => filter.enabled).length;
  elements.filterStatus.textContent = activeCount + '/' + Object.keys(filters).length + ' on';
  elements.researchFilterFields.innerHTML = researchFilterGroups.map((group) =>
    '<section class="filter-group"><div class="filter-group-heading"><h3>' + escapeHtml(group.title) +
      '</h3><p>' + escapeHtml(group.description) + '</p></div><div class="filter-controls">' +
      group.filters.map((definition) => {
        const filter = filters[definition.key];
        if (!filter) return '';
        const inputFields = definition.fields.map(([field, label]) =>
          '<label class="filter-number"><span>' + escapeHtml(label) + '</span>' +
          '<input type="number" min="0" step="any" data-filter-key="' + escapeHtml(definition.key) +
          '" data-filter-field="' + escapeHtml(field) + '" value="' + escapeHtml(displayFilterNumber(filter[field])) +
          '"' + (filter.enabled ? '' : ' disabled') + '></label>',
        ).join('');
        return '<article class="filter-control" data-filter-control="' + escapeHtml(definition.key) + '">' +
          '<label class="filter-toggle"><input type="checkbox" data-filter-toggle="' + escapeHtml(definition.key) +
          '"' + (filter.enabled ? ' checked' : '') + '><span>' + escapeHtml(definition.label) + '</span></label>' +
          (inputFields ? '<div class="filter-number-fields">' + inputFields + '</div>' : '<small>On/off research gate</small>') +
          '</article>';
      }).join('') + '</div></section>',
  ).join('');
}

function collectResearchFilters() {
  const filters = JSON.parse(JSON.stringify(state.researchFilters));
  elements.researchFilterFields.querySelectorAll('[data-filter-toggle]').forEach((input) => {
    filters[input.dataset.filterToggle].enabled = input.checked;
  });
  elements.researchFilterFields.querySelectorAll('[data-filter-key][data-filter-field]').forEach((input) => {
    const value = Number(input.value);
    if (!Number.isFinite(value)) throw new Error('Every enabled limit needs a number.');
    filters[input.dataset.filterKey][input.dataset.filterField] = value;
  });
  return filters;
}

function setFilterInputsDisabled(key, disabled) {
  elements.researchFilterFields.querySelectorAll('[data-filter-key="' + CSS.escape(key) + '"]').forEach((input) => {
    input.disabled = disabled;
  });
}

async function loadResearchFilters() {
  const response = await fetch('/api/research-filters');
  if (!response.ok) throw new Error('Could not load the research-filter settings.');
  const result = await response.json();
  state.researchFilters = result.filters;
  renderResearchFilters();
}

async function saveResearchFilters(event) {
  event.preventDefault();
  try {
    const filters = collectResearchFilters();
    elements.saveResearchFilters.disabled = true;
    const response = await fetch('/api/research-filters', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filters }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Could not save research filters.');
    state.researchFilters = result.filters;
    renderResearchFilters();
    elements.note.textContent = 'Filters saved. Run a new scan to use them; existing cards have been re-rated using the new limits.';
    await loadCandidates();
  } catch (error) {
    elements.note.textContent = error.message;
  } finally {
    elements.saveResearchFilters.disabled = false;
  }
}

async function resetResearchFilters() {
  if (!window.confirm('Reset every research filter to the starter limits?')) return;
  elements.resetResearchFilters.disabled = true;
  try {
    const response = await fetch('/api/research-filters/reset', { method: 'POST' });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Could not reset research filters.');
    state.researchFilters = result.filters;
    renderResearchFilters();
    elements.note.textContent = result.note;
    await loadCandidates();
  } catch (error) {
    elements.note.textContent = error.message;
  } finally {
    elements.resetResearchFilters.disabled = false;
  }
}

function visibleCandidates() {
  return state.filter === 'all'
    ? state.candidates
    : state.candidates.filter((candidate) => candidate.status === state.filter);
}

function selectedCandidate() {
  return state.candidates.find((candidate) => candidate.mint === state.selectedMint)
    || visibleCandidates()[0]
    || state.candidates[0]
    || null;
}

function renderMetrics() {
  if (!state.data) {
    elements.metrics.innerHTML = '';
    return;
  }
  const summary = state.data.summary;
  const metrics = [
    ['Candidate', summary.candidate, 'passed the available gates'],
    ['Watch', summary.watch, 'needs more confirmation'],
    ['Avoid', summary.avoid, 'failed at least one hard gate'],
    state.data.is_sample_data
      ? ['Data source', summary.sample + ' sample', 'click for public pair data']
      : ['Data source', summary.live + ' live', 'DEX Screener public data'],
  ];
  elements.metrics.innerHTML = metrics.map((metric) =>
    '<article class="metric">' +
      '<span>' + escapeHtml(metric[0]) + '</span>' +
      '<strong>' + escapeHtml(metric[1]) + '</strong>' +
      '<small>' + escapeHtml(metric[2]) + '</small>' +
    '</article>',
  ).join('');
}

function renderFilters() {
  const filters = [
    ['all', 'All'],
    ['candidate', 'Candidates'],
    ['watch', 'Watch'],
    ['avoid', 'Avoid'],
  ];
  elements.filters.innerHTML = filters.map((filter) =>
    '<button type="button" class="filter ' + (state.filter === filter[0] ? 'is-active' : '') +
      '" data-filter="' + filter[0] + '">' + filter[1] + '</button>',
  ).join('');
}

function renderCandidateList() {
  const candidates = visibleCandidates();
  if (!candidates.length) {
    elements.list.innerHTML = '<div class="empty">No cards match this filter.</div>';
    return;
  }

  elements.list.innerHTML = candidates.map((candidate) => {
    const isSelected = selectedCandidate()?.mint === candidate.mint;
    const reasons = candidate.reasons.slice(0, 2).map((reason) =>
      '<li>' + escapeHtml(reason) + '</li>',
    ).join('');
    return (
      '<button type="button" class="candidate-card ' + candidate.status +
        (isSelected ? ' is-selected' : '') + '" data-mint="' + escapeHtml(candidate.mint) + '">' +
        '<div class="candidate-card-top">' +
          '<div><p class="setup">' + escapeHtml(formatSetup(candidate.setup)) + '</p>' +
          '<h3>' + escapeHtml(candidate.name) + ' <span>$' + escapeHtml(candidate.symbol) + '</span></h3></div>' +
          '<span class="status-chip ' + candidate.status + '">' + statusLabels[candidate.status] + '</span>' +
        '</div>' +
        '<div class="candidate-stats">' +
          '<span><b>' + formatCompactUsd(candidate.market_cap_usd) + '</b> market cap</span>' +
          '<span><b>' + formatCompactUsd(candidate.liquidity_usd) + '</b> liquidity</span>' +
          '<span><b>' + formatPercent(candidate.sell_impact_pct) + '</b> sell impact</span>' +
        '</div>' +
        '<div class="score-row"><span>Research score</span><strong>' + candidate.score + '/100</strong>' +
          '<i><em style="width:' + candidate.score + '%"></em></i></div>' +
        '<ul class="card-reasons">' + reasons + '</ul>' +
      '</button>'
    );
  }).join('');
}

function metricCell(label, value, emphasis = '') {
  return '<div class="detail-metric ' + emphasis + '"><span>' + escapeHtml(label) +
    '</span><strong>' + value + '</strong></div>';
}

function renderDetail() {
  const candidate = selectedCandidate();
  if (!candidate) {
    elements.detail.innerHTML = '<div class="empty">Select a candidate to see its research card.</div>';
    return;
  }
  const drawdown = candidate.derived.drawdown_pct === null
    ? '—'
    : '−' + formatPercent(candidate.derived.drawdown_pct);
  const gates = candidate.gates.map((gate) =>
    '<li class="gate ' + gate.status.toLowerCase() + '">' +
      '<div><b>' + escapeHtml(gate.label) + '</b><small>' + escapeHtml(gate.detail) + '</small></div>' +
      '<span>' + escapeHtml(gate.status) + '</span></li>',
  ).join('');
  const reasons = candidate.reasons.map((reason) => '<li>' + escapeHtml(reason) + '</li>').join('');
  const warnings = candidate.warnings.map((warning) => '<li>' + escapeHtml(warning) + '</li>').join('');
  const safetyNote = candidate.source === 'sample'
    ? '<p class="sample-note">This is fictional sample data. It does not mean the checks passed for a real token.</p>'
    : '';
  const walletEvidence = candidate.wallet_evidence || {};
  const riskEvidence = candidate.risk_evidence || {};
  const trackerMetrics = riskEvidence.risk_score === undefined ? '' :
    ' · top 10 ' + formatPercent(riskEvidence.top_10_holder_pct) +
    ' · dev ' + formatPercent(riskEvidence.developer_holder_pct) +
    ' · insiders ' + formatPercent(riskEvidence.insider_holder_pct) +
    ' · bundlers ' + formatPercent(riskEvidence.bundler_holder_pct) +
    ' · snipers ' + formatPercent(riskEvidence.sniper_holder_pct) +
    ' · LP/curve: ' + escapeHtml(riskEvidence.lp_or_curve_status || 'not returned');
  const providerEvidence = candidate.source === 'sample' ? '' :
    '<section class="detail-section"><h3>Provider evidence</h3><ul class="detail-list">' +
      '<li><b>Jupiter:</b> ' + escapeHtml(candidate.sell_quote_status || 'pending') +
        (candidate.sell_quote_note ? ' — ' + escapeHtml(candidate.sell_quote_note) : '') + '</li>' +
      '<li><b>Solana Tracker:</b> ' + escapeHtml(candidate.safety_status || 'pending') +
        (candidate.safety_score !== null && candidate.safety_score !== undefined ? ' · risk score ' + escapeHtml(candidate.safety_score) + '/10' : '') + trackerMetrics + '</li>' +
      '<li><b>Helius:</b> public creator/authority ' + escapeHtml(shortAddress(candidate.creator_address)) +
        (walletEvidence.recent_token_outflows_from_observed_address !== undefined ? ' · observed token outflows ' + escapeHtml(walletEvidence.recent_token_outflows_from_observed_address) : '') + '</li>' +
      '<li><b>CoinGecko:</b> ' + escapeHtml(candidate.crosscheck_status || 'pending') +
        (candidate.crosscheck_price_usd ? ' · pool price ' + formatUsd(candidate.crosscheck_price_usd) : '') + '</li>' +
    '</ul></section>';

  elements.detail.innerHTML =
    '<div class="detail-heading">' +
      '<div><p class="eyebrow">' + escapeHtml(formatSetup(candidate.setup)) + '</p>' +
      '<h2>' + escapeHtml(candidate.name) + ' <span>$' + escapeHtml(candidate.symbol) + '</span></h2>' +
      '<p class="muted">Solana · ' + Number(candidate.age_minutes).toFixed(0) + ' minutes observed</p></div>' +
      '<span class="status-chip ' + candidate.status + '">' + statusLabels[candidate.status] + '</span>' +
    '</div>' +
    '<div class="detail-score"><span>Research score</span><strong>' + candidate.score + '<small>/100</small></strong></div>' +
    '<div class="detail-metrics">' +
      metricCell('Market cap', formatUsd(candidate.market_cap_usd)) +
      metricCell('Liquidity', formatUsd(candidate.liquidity_usd)) +
      metricCell('Liquidity / MC', formatPercent(candidate.derived.liquidity_ratio_pct)) +
      metricCell('5m volume', formatUsd(candidate.volume_5m_usd)) +
      metricCell('Buy / sell', candidate.derived.buy_sell_ratio.toFixed(2) + '×') +
      metricCell('5m change', (candidate.price_change_5m_pct >= 0 ? '+' : '') + formatPercent(candidate.price_change_5m_pct), candidate.price_change_5m_pct >= 0 ? 'positive' : 'negative') +
      metricCell('Sell impact', formatPercent(candidate.sell_impact_pct)) +
      metricCell('Peak drawdown', drawdown) +
    '</div>' +
    '<section class="detail-section"><h3>Why it appeared</h3><ul class="detail-list">' + reasons + '</ul></section>' +
    '<section class="detail-section"><h3>Risk gates</h3><ul class="gates">' + gates + '</ul></section>' +
    providerEvidence +
    (warnings ? '<section class="detail-section warning-box"><h3>What still needs checking</h3><ul class="detail-list">' + warnings + '</ul></section>' : '') +
    safetyNote;
}

function render() {
  renderMetrics();
  renderFilters();
  renderCandidateList();
  renderDetail();
  if (state.data) elements.note.textContent = state.data.note;
}

async function loadCandidates({ keepButtonDisabled = false } = {}) {
  elements.dataStatus.textContent = 'Loading…';
  if (!keepButtonDisabled) elements.refresh.disabled = true;
  try {
    const response = await fetch('/api/candidates');
    if (!response.ok) throw new Error('Candidate feed returned ' + response.status);
    state.data = await response.json();
    state.candidates = state.data.candidates;
    if (!state.selectedMint || !state.candidates.some((candidate) => candidate.mint === state.selectedMint)) {
      state.selectedMint = state.candidates[0]?.mint || null;
    }
    elements.dataStatus.textContent = state.data.is_sample_data ? 'Sample data · local only' : 'Live research data';
    render();
  } catch (error) {
    elements.dataStatus.textContent = 'Feed unavailable';
    elements.list.innerHTML = '<div class="empty">Could not load the local candidate feed. Start the server, then refresh.</div>';
    elements.detail.innerHTML = '<div class="empty">' + escapeHtml(error.message) + '</div>';
  } finally {
    if (!keepButtonDisabled) elements.refresh.disabled = false;
  }
}

function setResearchControlsDisabled(disabled) {
  [elements.fullScan, elements.refresh, elements.quoteCheck, elements.safetyCheck,
    elements.walletCheck, elements.crosscheck, elements.addressSearch].forEach((button) => {
    button.disabled = disabled;
  });
  elements.addressInput.disabled = disabled;
}

async function researchAddress() {
  const address = elements.addressInput.value.trim();
  if (!address) {
    elements.note.textContent = 'Paste a Solana token mint address first.';
    return;
  }
  setResearchControlsDisabled(true);
  elements.dataStatus.textContent = 'Researching pasted Solana address…';
  elements.scanSummary.textContent = 'Loading its active pair, sell route, risk, public wallet evidence, and market cross-check…';
  try {
    const response = await fetch('/api/candidates/lookup', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ address }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Address research returned ' + response.status);
    state.selectedMint = result.candidate.mint;
    await loadCandidates({ keepButtonDisabled: true });
    elements.dataStatus.textContent = 'Address research: ' + result.candidate.symbol;
    elements.scanSummary.textContent = result.note;
    elements.note.textContent = result.note;
  } catch (error) {
    elements.dataStatus.textContent = 'Address research unavailable';
    elements.scanSummary.textContent = error.message;
    elements.note.textContent = error.message;
  } finally {
    setResearchControlsDisabled(false);
  }
}

async function runFullResearchScan() {
  setResearchControlsDisabled(true);
  elements.dataStatus.textContent = 'Running full research scan…';
  elements.scanSummary.textContent = 'Finding pairs, checking sellability, safety, public wallet evidence, and market data…';
  try {
    const response = await fetch('/api/candidates/full-scan', { method: 'POST' });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Full research scan returned ' + response.status);
    await loadCandidates({ keepButtonDisabled: true });
    elements.dataStatus.textContent = result.summary.headline;
    elements.scanSummary.textContent = result.summary.detail;
    elements.note.textContent = result.summary.detail;
  } catch (error) {
    elements.dataStatus.textContent = 'Full scan unavailable';
    elements.scanSummary.textContent = error.message;
    elements.note.textContent = error.message;
  } finally {
    setResearchControlsDisabled(false);
  }
}

async function refreshCandidates() {
  setResearchControlsDisabled(true);
  elements.dataStatus.textContent = 'Getting public Solana pairs…';
  try {
    const response = await fetch('/api/candidates/refresh', { method: 'POST' });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Live refresh returned ' + response.status);
    await loadCandidates({ keepButtonDisabled: true });
    const coverage = result.coverage || {};
    const nearMisses = (coverage.common_failures || [])
      .map((item) => item.label + ' (' + item.count + ')').join(', ');
    elements.dataStatus.textContent = result.records_saved
      ? 'Loaded ' + result.records_saved + ' live research cards'
      : (coverage.pairs_seen
        ? 'Checked ' + coverage.pairs_seen + ' current pairs · none passed every starter rule'
        : 'No usable current pair data returned yet');
    if (!result.records_saved && coverage.pairs_seen) {
      elements.note.textContent = nearMisses
        ? 'Most common near-miss gates: ' + nearMisses + '.'
        : 'No pair passed every starter rule in this scan.';
    }
  } catch (error) {
    elements.dataStatus.textContent = 'Live refresh unavailable';
    elements.note.textContent = error.message;
  } finally {
    setResearchControlsDisabled(false);
  }
}

async function checkSellRoutes() {
  setResearchControlsDisabled(true);
  elements.dataStatus.textContent = 'Checking small sell routes…';
  try {
    const response = await fetch('/api/candidates/quote-check', { method: 'POST' });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Jupiter quote check returned ' + response.status);
    await loadCandidates({ keepButtonDisabled: true });
    elements.dataStatus.textContent = result.checked
      ? 'Checked ' + result.checked + ' sell routes · ' + result.sellable + ' found'
      : (result.note || '').includes('JUPITER_API_KEY')
        ? 'Jupiter key setup needed'
        : 'No live cards available to quote';
    elements.note.textContent = result.note;
  } catch (error) {
    elements.dataStatus.textContent = 'Sell-route check unavailable';
    elements.note.textContent = error.message;
  } finally {
    setResearchControlsDisabled(false);
  }
}

async function checkTokenSafety() {
  setResearchControlsDisabled(true);
  elements.dataStatus.textContent = 'Checking token safety…';
  try {
    const response = await fetch('/api/candidates/safety-check', { method: 'POST' });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Safety check returned ' + response.status);
    await loadCandidates({ keepButtonDisabled: true });
    elements.dataStatus.textContent = result.checked
      ? 'Checked ' + result.checked + ' tokens · ' + result.flagged + ' avoids · ' + (result.watch || 0) + ' watch warnings'
      : (result.note || '').includes('SOLANA_TRACKER_API_KEY')
        ? 'Safety-check setup needed'
        : 'No sellable cards to safety-check';
    elements.note.textContent = result.note;
  } catch (error) {
    elements.dataStatus.textContent = 'Safety check unavailable';
    elements.note.textContent = error.message;
  } finally {
    setResearchControlsDisabled(false);
  }
}

async function checkWalletEvidence() {
  setResearchControlsDisabled(true);
  elements.dataStatus.textContent = 'Checking public wallet evidence…';
  try {
    const response = await fetch('/api/candidates/wallet-check', { method: 'POST' });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Wallet evidence check returned ' + response.status);
    await loadCandidates({ keepButtonDisabled: true });
    elements.dataStatus.textContent = result.checked
      ? 'Checked ' + result.checked + ' public wallet records'
      : (result.note || '').includes('HELIUS_API_KEY')
        ? 'Wallet-evidence setup needed'
        : 'No clean cards for wallet evidence';
    elements.note.textContent = result.note;
  } catch (error) {
    elements.dataStatus.textContent = 'Wallet evidence unavailable';
    elements.note.textContent = error.message;
  } finally {
    setResearchControlsDisabled(false);
  }
}

async function crosscheckMarketData() {
  setResearchControlsDisabled(true);
  elements.dataStatus.textContent = 'Cross-checking pool data…';
  try {
    const response = await fetch('/api/candidates/crosscheck', { method: 'POST' });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Market cross-check returned ' + response.status);
    await loadCandidates({ keepButtonDisabled: true });
    elements.dataStatus.textContent = result.checked
      ? 'Cross-checked ' + result.checked + ' pools · ' + result.consistent + ' consistent'
      : (result.note || '').includes('COINGECKO_DEMO_API_KEY')
        ? 'Cross-check setup needed'
        : 'No fully screened cards to cross-check';
    elements.note.textContent = result.note;
  } catch (error) {
    elements.dataStatus.textContent = 'Cross-check unavailable';
    elements.note.textContent = error.message;
  } finally {
    setResearchControlsDisabled(false);
  }
}

elements.filters.addEventListener('click', (event) => {
  const button = event.target.closest('[data-filter]');
  if (!button) return;
  state.filter = button.dataset.filter;
  const next = selectedCandidate();
  state.selectedMint = next?.mint || null;
  render();
});

elements.list.addEventListener('click', (event) => {
  const card = event.target.closest('[data-mint]');
  if (!card) return;
  state.selectedMint = card.dataset.mint;
  renderCandidateList();
  renderDetail();
});

elements.fullScan.addEventListener('click', runFullResearchScan);
elements.addressSearch.addEventListener('click', researchAddress);
elements.addressInput.addEventListener('keydown', (event) => {
  if (event.key === 'Enter') researchAddress();
});
elements.refresh.addEventListener('click', refreshCandidates);
elements.quoteCheck.addEventListener('click', checkSellRoutes);
elements.safetyCheck.addEventListener('click', checkTokenSafety);
elements.walletCheck.addEventListener('click', checkWalletEvidence);
elements.crosscheck.addEventListener('click', crosscheckMarketData);
elements.researchFilterForm.addEventListener('submit', saveResearchFilters);
elements.resetResearchFilters.addEventListener('click', resetResearchFilters);
elements.researchFilterFields.addEventListener('change', (event) => {
  const toggle = event.target.closest('[data-filter-toggle]');
  if (!toggle) return;
  setFilterInputsDisabled(toggle.dataset.filterToggle, !toggle.checked);
});

async function initialize() {
  try {
    await loadResearchFilters();
  } catch (error) {
    elements.filterStatus.textContent = 'Unavailable';
    elements.note.textContent = error.message;
  }
  await loadCandidates();
}

initialize();
