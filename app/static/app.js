const chatArea = document.getElementById('chat');
const form = document.getElementById('query-form');
const input = document.getElementById('question-input');
const submitBtn = document.getElementById('submit-btn');
const newChatBtn = document.getElementById('new-chat-btn');
const settingsBtn = document.getElementById('settings-btn');
const settingsPanel = document.getElementById('settings-panel');
const settingsCloseBtn = document.getElementById('settings-close-btn');
const commentaryTextarea = document.getElementById('commentary-prompt');
const nlToSqlTextarea = document.getElementById('nl-to-sql-prompt');
const resetPromptBtn = document.getElementById('reset-prompt-btn');
const resetNlPromptBtn = document.getElementById('reset-nl-prompt-btn');
const savePromptBtn = document.getElementById('save-prompt-btn');

const API_URL = '/api/v1';

// ── Default prompts ──────────────────────────────────────────────────────────
const DEFAULT_COMMENTARY_PROMPT = `You are a data analyst providing concise, insightful commentary on financial query results from a transactional database.

Given the user's original question, the SQL query that was executed, and the results, provide a brief analysis that:

1. DIRECTLY answers the user's question in plain language (first sentence).
2. Highlights notable patterns, trends, or outliers in the data.
3. Provides context (e.g., percentages, comparisons, rankings).
4. Suggests follow-up questions the user might want to explore.

RULES:
- Be concise: 3-5 sentences maximum for the main insight.
- Use specific numbers from the results.
- If results are empty, explain what that likely means.
- Do not repeat the raw data; summarize and interpret it.
- Format numbers with appropriate precision (e.g., $1.2M, not $1,234,567.89).
- Use bullet points for multiple insights.`;

const DEFAULT_NL_TO_SQL_PROMPT = `You are a SQL Server query generator. Your job is to convert natural language questions into valid T-SQL SELECT queries.

SCOPE CHECK (apply this FIRST before anything else):
If the question is NOT about the data inside this database, respond as follows:
- If you can answer it as brief general knowledge (math, definitions, geography, simple facts): respond with OUT_OF_SCOPE:<your concise 1-2 sentence answer>
- For anything else (weather forecasts, news, personal advice, opinions): respond with exactly: OUT_OF_SCOPE

RULES (only if the question IS database-related):
1. Generate ONLY SELECT statements. Never generate INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, TRUNCATE, EXEC, or any other non-SELECT statement.
2. Always use fully qualified names: [dbo].[transactionaldata] is the main view.
3. Use TOP instead of LIMIT (T-SQL syntax).
4. Use column aliases for clarity.
5. When the question is ambiguous, prefer the simplest reasonable interpretation.
6. Do NOT use JOINs — all data is pre-joined in the [dbo].[transactionaldata] view.
7. For date/period filtering, the view has these columns:
   - period     — original format e.g. "Jan2022"
   - full_month — full month name e.g. "January"
   - short_month — 3-letter abbreviation e.g. "Jan"
   - year       — 4-digit year e.g. "2022"
   Use these directly instead of DATEADD/DATEDIFF.
8. Always include an ORDER BY clause when using TOP or when results have a natural ordering.
9. Do NOT use semicolons at the end.
10. Respond with ONLY the SQL query. No explanations, no markdown fencing, no commentary.
11. CATEGORY/TAG COLUMN (CRITICAL): When a question refers to a category, type, or classification
    of accounts (e.g. "liabilities", "assets", "revenue", "expenses", "equity"), use the tag column.
    The tag column stores values like "Loans, Liability", "Interest Income, Revenue",
    "Fixed Assets - Development Costs, Asset", "Input VAT, Asset".
    The category is ALWAYS the last word/phrase after the final comma.
    Therefore ALWAYS filter using: WHERE tag LIKE '%, Liability'  (not = 'Liability')
    Examples:
      - liabilities / liability  → WHERE tag LIKE '%, Liability'
      - assets / asset           → WHERE tag LIKE '%, Asset'
      - revenue / income         → WHERE tag LIKE '%, Revenue'
      - expenses / costs         → WHERE tag LIKE '%, Expense'
      - equity                   → WHERE tag LIKE '%, Equity'
      - other                    → WHERE tag LIKE '%, Other'
12. AVAILABLE COLUMNS in [dbo].[transactionaldata]:
    - legal_entity_id   — numeric entity ID
    - legal_entity_name — entity name (e.g. "Contoso Ltd")
    - account_id        — numeric account ID
    - account_description — account name (e.g. "Interest Income")
    - value             — transaction monetary amount
    - period            — e.g. "Jan2022"
    - data_type         — data type ID
    - data_type_desc    — data type description
    - transaction_type  — transaction type ID
    - transaction_type_desc — transaction type description
    - full_month        — e.g. "January"
    - short_month       — e.g. "Jan"
    - year              — e.g. "2022"
    - tag               — account classification e.g. "Loans, Liability"

DATABASE SCHEMA:
{schema}

FEW-SHOT EXAMPLES:
{examples}
{history}`;

// ── Prompt settings (load from localStorage) ────────────────────────────────
let customCommentaryPrompt = localStorage.getItem('commentary_prompt') || null;
let customNlToSqlPrompt = localStorage.getItem('nl_to_sql_prompt') || null;

commentaryTextarea.value = customCommentaryPrompt || DEFAULT_COMMENTARY_PROMPT;
nlToSqlTextarea.value = customNlToSqlPrompt || DEFAULT_NL_TO_SQL_PROMPT;

function toggleSettings() {
    settingsPanel.style.display = settingsPanel.style.display === 'none' ? 'block' : 'none';
}

settingsBtn.addEventListener('click', toggleSettings);

settingsCloseBtn.addEventListener('click', () => {
    settingsPanel.style.display = 'none';
});

savePromptBtn.addEventListener('click', () => {
    const cVal = commentaryTextarea.value.trim();
    if (cVal && cVal !== DEFAULT_COMMENTARY_PROMPT) {
        customCommentaryPrompt = cVal;
        localStorage.setItem('commentary_prompt', cVal);
    } else {
        customCommentaryPrompt = null;
        localStorage.removeItem('commentary_prompt');
    }

    const nVal = nlToSqlTextarea.value.trim();
    if (nVal && nVal !== DEFAULT_NL_TO_SQL_PROMPT) {
        customNlToSqlPrompt = nVal;
        localStorage.setItem('nl_to_sql_prompt', nVal);
    } else {
        customNlToSqlPrompt = null;
        localStorage.removeItem('nl_to_sql_prompt');
    }

    settingsPanel.style.display = 'none';
});

resetPromptBtn.addEventListener('click', () => {
    commentaryTextarea.value = DEFAULT_COMMENTARY_PROMPT;
    customCommentaryPrompt = null;
    localStorage.removeItem('commentary_prompt');
});

resetNlPromptBtn.addEventListener('click', () => {
    nlToSqlTextarea.value = DEFAULT_NL_TO_SQL_PROMPT;
    customNlToSqlPrompt = null;
    localStorage.removeItem('nl_to_sql_prompt');
});

// ── Session management ────────────────────────────────────────────────────────
function generateSessionId() {
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
        const r = Math.random() * 16 | 0;
        return (c === 'x' ? r : (r & 0x3 | 0x8)).toString(16);
    });
}

let sessionId = generateSessionId();

newChatBtn.addEventListener('click', () => {
    sessionId = generateSessionId();
    chatArea.innerHTML = `
        <div class="message bot">
            <div class="message-content">
                New conversation started. Hello! I can help you query your database using natural language.
                Try asking something like <em>"How many transactions are in the database?"</em>
            </div>
        </div>`;
    input.focus();
});
// ─────────────────────────────────────────────────────────────────────────────

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
            body: JSON.stringify({ question, page: 1, page_size: 100, include_commentary: true, session_id: sessionId, commentary_prompt: customCommentaryPrompt, nl_to_sql_prompt: customNlToSqlPrompt }),
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
            body: JSON.stringify({ question, page, page_size: pageSize, include_commentary: false, session_id: sessionId }),
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
