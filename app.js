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
  refresh: document.querySelector('#refresh'),
  note: document.querySelector('#feed-note'),
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
    ['Candidate', summary.candidate, 'passed the sample gates'],
    ['Watch', summary.watch, 'needs more confirmation'],
    ['Avoid', summary.avoid, 'failed at least one hard gate'],
    ['Data source', summary.sample + ' sample', 'live APIs come next'],
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

async function loadCandidates() {
  elements.dataStatus.textContent = 'Loading…';
  elements.refresh.disabled = true;
  try {
    const response = await fetch('/api/candidates');
    if (!response.ok) throw new Error('Candidate feed returned ' + response.status);
    state.data = await response.json();
    state.candidates = state.data.candidates;
    if (!state.selectedMint || !state.candidates.some((candidate) => candidate.mint === state.selectedMint)) {
      state.selectedMint = state.candidates[0]?.mint || null;
    }
    elements.dataStatus.textContent = state.data.is_sample_data ? 'Sample data · local only' : 'Live data';
    render();
  } catch (error) {
    elements.dataStatus.textContent = 'Feed unavailable';
    elements.list.innerHTML = '<div class="empty">Could not load the local candidate feed. Start the server, then refresh.</div>';
    elements.detail.innerHTML = '<div class="empty">' + escapeHtml(error.message) + '</div>';
  } finally {
    elements.refresh.disabled = false;
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

elements.refresh.addEventListener('click', loadCandidates);
loadCandidates();
