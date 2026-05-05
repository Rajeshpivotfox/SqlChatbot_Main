const chatArea = document.getElementById('chat');
const form = document.getElementById('query-form');
const input = document.getElementById('question-input');
const submitBtn = document.getElementById('submit-btn');

const API_URL = '/api/v1';

form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const question = input.value.trim();
    if (!question) return;

    addMessage(question, 'user');
    input.value = '';
    submitBtn.disabled = true;

    const loadingEl = addLoading();

    try {
        const response = await fetch(`${API_URL}/query`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ question, page: 1, page_size: 100, include_commentary: true }),
        });

        loadingEl.remove();

        if (!response.ok) {
            const err = await response.json();
            const detail = err.detail || {};
            addError(detail.message || `Error ${response.status}: Something went wrong.`);
            return;
        }

        const data = await response.json();
        addResultMessage(data);
    } catch (err) {
        loadingEl.remove();
        addError('Network error. Please check if the server is running.');
    } finally {
        submitBtn.disabled = false;
        input.focus();
    }
});

function addMessage(text, sender) {
    const div = document.createElement('div');
    div.className = `message ${sender}`;
    div.innerHTML = `<div class="message-content">${escapeHtml(text)}</div>`;
    chatArea.appendChild(div);
    chatArea.scrollTop = chatArea.scrollHeight;
}

function addLoading() {
    const div = document.createElement('div');
    div.className = 'message bot';
    div.innerHTML = `<div class="message-content"><div class="loading"><span></span><span></span><span></span></div></div>`;
    chatArea.appendChild(div);
    chatArea.scrollTop = chatArea.scrollHeight;
    return div;
}

function addError(message) {
    const div = document.createElement('div');
    div.className = 'message bot';
    div.innerHTML = `<div class="message-content"><div class="error">${escapeHtml(message)}</div></div>`;
    chatArea.appendChild(div);
    chatArea.scrollTop = chatArea.scrollHeight;
}

function addResultMessage(data) {
    const div = document.createElement('div');
    div.className = 'message bot';

    let html = '<div class="message-content">';

    // Out-of-scope: show friendly redirect message only
    if (data.out_of_scope) {
        const lines = (data.commentary || '').split('\n').map(l => escapeHtml(l)).join('<br>');
        html += `<div class="out-of-scope">${lines}</div>`;
        html += '</div>';
        div.innerHTML = html;
        chatArea.appendChild(div);
        chatArea.scrollTop = chatArea.scrollHeight;
        return;
    }

    // SQL block
    html += `<div class="sql-block">${escapeHtml(data.generated_sql)}</div>`;

    // Results table
    if (data.rows && data.rows.length > 0) {
        html += '<div class="table-wrapper"><table class="results-table"><thead><tr>';
        data.columns.forEach(col => {
            html += `<th>${escapeHtml(col.name)}</th>`;
        });
        html += '</tr></thead><tbody>';
        data.rows.forEach(row => {
            html += '<tr>';
            data.columns.forEach(col => {
                const val = row[col.name];
                html += `<td>${escapeHtml(val != null ? String(val) : '')}</td>`;
            });
            html += '</tr>';
        });
        html += '</tbody></table></div>';
    } else {
        html += '<p><em>No results returned.</em></p>';
    }

    // Commentary
    if (data.commentary) {
        html += `<div class="commentary">${escapeHtml(data.commentary)}</div>`;
    }

    // Timing breakdown
    if (data.timing_breakdown && Object.keys(data.timing_breakdown).length > 0) {
        html += buildTimingPanel(data.timing_breakdown);
    }

    // Metadata
    html += `<div class="meta">${data.total_rows} total rows | ${data.execution_time_ms.toFixed(0)}ms total`;
    if (data.has_more) {
        html += ` | Page ${data.page}`;
    }
    html += '</div>';

    // Pagination
    if (data.has_more) {
        html += `<div class="pagination">`;
        html += `<button onclick="loadPage('${escapeAttr(data.question)}', ${data.page + 1}, ${data.page_size})">Next Page</button>`;
        html += `</div>`;
    }

    html += '</div>';
    div.innerHTML = html;
    chatArea.appendChild(div);
    chatArea.scrollTop = chatArea.scrollHeight;
}

function buildTimingPanel(timing) {
    const steps = [
        { key: 'nl_to_sql_ms',      label: '🤖 NL → SQL (Claude)',   color: '#4361ee' },
        { key: 'sql_execution_ms',  label: '🗄️ SQL Execution',        color: '#27ae60' },
        { key: 'commentary_ms',     label: '💬 Commentary (Claude)',  color: '#8e44ad' },
        { key: 'validation_ms',     label: '🔒 SQL Validation',       color: '#e67e22' },
        { key: 'formatting_ms',     label: '📋 Formatting',           color: '#16a085' },
        { key: 'cache_check_ms',    label: '⚡ Cache Check',           color: '#95a5a6' },
    ];

    const total = timing.total_ms || 1;
    const present = steps.filter(s => timing[s.key] !== undefined && timing[s.key] > 0);
    if (present.length === 0) return '';

    let html = `<details class="timing-panel">`;
    html += `<summary class="timing-summary">⏱ ${total.toFixed(0)}ms total &mdash; click to see breakdown</summary>`;
    html += `<div class="timing-rows">`;

    for (const step of present) {
        const ms = timing[step.key];
        const pct = Math.min(100, (ms / total) * 100);
        const pctLabel = pct < 1 ? '<1' : pct.toFixed(0);
        html += `
        <div class="timing-row">
          <span class="timing-label">${step.label}</span>
          <div class="timing-bar-wrap">
            <div class="timing-bar" style="width:${pct}%;background:${step.color}"></div>
          </div>
          <span class="timing-val">${ms.toFixed(0)}ms</span>
          <span class="timing-pct">${pctLabel}%</span>
        </div>`;
    }

    html += `</div></details>`;
    return html;
}

async function loadPage(question, page, pageSize) {
    const loadingEl = addLoading();
    try {
        const response = await fetch(`${API_URL}/query`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ question, page, page_size: pageSize, include_commentary: false }),
        });
        loadingEl.remove();

        if (!response.ok) {
            const err = await response.json();
            addError(err.detail?.message || 'Failed to load page.');
            return;
        }

        const data = await response.json();
        addResultMessage(data);
    } catch (err) {
        loadingEl.remove();
        addError('Network error loading page.');
    }
}

function escapeHtml(text) {
    const d = document.createElement('div');
    d.textContent = text;
    return d.innerHTML;
}

function escapeAttr(text) {
    return text.replace(/'/g, "\\'").replace(/"/g, '&quot;');
}
