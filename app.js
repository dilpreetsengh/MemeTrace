const state = {
  data: null,
  candidates: [],
  filter: 'all',
  selectedMint: null,
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
  refresh: document.querySelector('#refresh'),
  quoteCheck: document.querySelector('#quote-check'),
  safetyCheck: document.querySelector('#safety-check'),
  walletCheck: document.querySelector('#wallet-check'),
  crosscheck: document.querySelector('#crosscheck'),
  note: document.querySelector('#feed-note'),
  scanSummary: document.querySelector('#scan-summary'),
};

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
    elements.walletCheck, elements.crosscheck].forEach((button) => {
    button.disabled = disabled;
  });
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
    elements.dataStatus.textContent = result.records_saved
      ? 'Loaded ' + result.records_saved + ' live research cards'
      : 'No pairs passed the starter filter yet';
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
elements.refresh.addEventListener('click', refreshCandidates);
elements.quoteCheck.addEventListener('click', checkSellRoutes);
elements.safetyCheck.addEventListener('click', checkTokenSafety);
elements.walletCheck.addEventListener('click', checkWalletEvidence);
elements.crosscheck.addEventListener('click', crosscheckMarketData);
loadCandidates();
