'use strict';

const DEFAULT_GRPC_ENDPOINT = localStorage.getItem('grpc-web-endpoint') || 'http://localhost:8080';

// Poll intervals (ms)
const POLL_MS_ORDERS = 2000;
const POLL_MS_PORTFOLIO = 3000;

// Result limits
const FILLS_LIMIT = 100;
const ORDERS_LIMIT = 200;

// string
let grpcEndpoint = DEFAULT_GRPC_ENDPOINT;
// window.TradingWebClient instance
let grpcClient = null;
// number
let pollTimerOrders = null;
// number
let pollTimerPortfolio = null;

// --- DOM helpers

/**
 * Shorthand alias for getElementById.
 * @param {string} id - Element id.
 * @returns {HTMLElement|null}
 */
function $(id) {
    return document.getElementById(id);
}

/**
 * Set text content if the element exists.
 * @param {string} id - Element id.
 * @param {string} text - Text to set.
 */
function setText(id, text) {
    const el = $(id);
    if (el) el.textContent = text;
}

/**
 * Query a selector.
 * @param {string} sel - CSS selector.
 * @returns {Element|null}
 */
function bySelector(sel) {
    return document.querySelector(sel);
}

/**
 * Update the status-dot preceding a label.
 * @param {string} labelId - Label element id (expects a dot before it).
 * @param {'is-good'|'is-warn'|'is-bad'} cls - Status class.
 */
function setStatusDotFor(labelId, cls) {
    const label = $(labelId);
    if (!label) return;
    const dot = label.previousElementSibling;
    if (!dot) return;
    dot.classList.remove('is-good', 'is-warn', 'is-bad');
    dot.classList.add(cls);
}

/**
 * Prepend a notification list item with a colored dot.
 * @param {string} message - Notification message.
 * @param {'info'|'warn'|'error'} [level='info'] - Visual level.
 */
function addNotification(message, level = 'info') {
    const list = $('notification-list');
    if (!list) return;
    const li = document.createElement('li');
    li.className = 'list-item';
    const dot = document.createElement('span');
    dot.className =
        'status-dot ' +
        (level === 'error' ? 'is-bad' : level === 'warn' ? 'is-warn' : 'is-good');
    dot.setAttribute('aria-hidden', 'true');
    li.appendChild(dot);
    li.appendChild(document.createTextNode(' ' + message));
    list.prepend(li);
}

/**
 * Remove all children from an element.
 * @param {Element|null} el - Element to clear.
 */
function empty(el) {
    while (el && el.firstChild) el.removeChild(el.firstChild);
}

/**
 * Render-friendly string for potentially empty values.
 * @param {any} v - Value.
 * @returns {string}
 */
function fmt(v) {
    return v === null || v === undefined || v === '' ? '—' : String(v);
}

/**
 * Return a badge HTML string appropriate for an order status.
 * @param {string} status - Order status.
 * @returns {string}
 */
function badgeForStatus(status) {
    const s = (status || '').toUpperCase();
    const cls =
        s === 'FILLED' ?
        'badge--success' :
        s === 'SUBMITTED' || s === 'PENDING' ?
        'badge--info' :
        s === 'CANCELLED' || s === 'REJECTED' ?
        '' :
        'badge--info';
    return `<span class="badge ${cls}">${fmt(status)}</span>`;
}

// --- gRPC Wiring

/**
 * Initialize the grpc-web client with an endpoint and trigger first loads.
 * Stores the endpoint in localStorage.
 * @param {string} [endpoint] - gRPC-web base URL (e.g. http://localhost:8080).
 */
function initGrpc(endpoint) {
    if (!window.TradingWebClient) {
        console.error('TradingWebClient not found. Ensure bundle.js is loaded first.');
        addNotification('Client missing (bundle.js not loaded)', 'error');
        return;
    }
    grpcEndpoint = (endpoint || '').trim() || DEFAULT_GRPC_ENDPOINT;
    localStorage.setItem('grpc-web-endpoint', grpcEndpoint);
    grpcClient = new window.TradingWebClient(grpcEndpoint, {
        format: 'text'
    });

    addNotification(`Using endpoint: ${grpcEndpoint}`, 'info');
    refreshOrdersAndFills();
    refreshPortfolio();
    setStatusDotFor('ib-connection-status', 'is-good');
    setStatusDotFor('drainer-status', 'is-good');
}

/**
 * Fetch and render orders & fills using proto-declared RPCs.
 * Uses: ListOrders, ListFills.
 * Updates IB status dot on success/failure.
 */
async function refreshOrdersAndFills() {
    if (!grpcClient) return;
    try {
        const [orders, fills] = await Promise.all([
            grpcClient.ListOrders(ORDERS_LIMIT),
            grpcClient.ListFills(undefined, FILLS_LIMIT),
        ]);
        renderOrders(orders);
        renderFills(fills);
        setStatusDotFor('ib-connection-status', 'is-good');
    } catch (err) {
        console.error(err);
        setStatusDotFor('ib-connection-status', 'is-bad');
        addNotification(
            `Orders/Fills load failed: ${err && err.message ? err.message : err}`,
            'error'
        );
    }
}

/**
 * Fetch and render positions & account values using proto-declared RPCs.
 * Uses: GetPositions, GetAccountValues.
 * Updates drainer status dot on success/failure.
 */
async function refreshPortfolio() {
    if (!grpcClient) return;
    try {
        const [positions, accountValues] = await Promise.all([
            grpcClient.GetPositions(),
            grpcClient.GetAccountValues(),
        ]);
        renderPositions(positions);
        renderAccountValues(accountValues);
        setStatusDotFor('drainer-status', 'is-good');
    } catch (err) {
        console.error(err);
        setStatusDotFor('drainer-status', 'is-bad');
        addNotification(
            `Portfolio load failed: ${err && err.message ? err.message : err}`,
            'error'
        );
    }
}

/**
 * Submit a manual market stock order via PlaceStockOrder.
 * Currently only supports MKT; LMT/STP are stubbed for Phase 1.
 */
async function placeManualOrder() {
    if (!grpcClient) return;
    const symbol = $('order-symbol').value.trim().toUpperCase();
    const side = $('order-side').value;
    const qty = parseInt($('order-qty').value, 10) || 0;
    const type = $('order-type').value;

    if (!symbol || !qty || qty <= 0) {
        addNotification('Enter a valid symbol and quantity', 'warn');
        return;
    }
    if (type !== 'MKT') {
        addNotification(`${type} orders not implemented in v0`, 'warn');
        return;
    }

    try {
        const resp = await grpcClient.PlaceStockOrder(symbol, side, qty);
        addNotification(
            `Order placed: #${resp.order_id} (${symbol} ${side} x${qty})`,
            'info'
        );
        refreshOrdersAndFills();
    } catch (err) {
        console.error(err);
        addNotification(`Order failed: ${err && err.message ? err.message : err}`, 'error');
    }
}

// --- Renderers

/**
 * Render the Orders table.
 * @param {Array<Object>} rows - Orders array from ListOrders.
 */
function renderOrders(rows) {
    const tbody = bySelector('#orders-table tbody');
    if (!tbody) return;
    empty(tbody);
    for (const r of rows) {
        const tr = document.createElement('tr');
        tr.innerHTML = `
      <td><input type="checkbox" /></td>
      <td>${fmt(r.order_id)}</td>
      <td>${fmt(r.created_at || '')}</td>
      <td>${fmt(r.symbol)}</td>
      <td>${fmt(r.side)}</td>
      <td>${fmt(r.order_type || 'MKT')}</td>
      <td>${fmt(r.quantity)}</td>
      <td>${fmt(r.limit_price || '')}</td>
      <td>${fmt(r.filled_qty)}</td>
      <td>${badgeForStatus(r.status)}</td>
      <td>${fmt(r.message || '')}</td>
    `;
        tbody.appendChild(tr);
    }
}

/**
 * Render the Fills table.
 * @param {Array<Object>} rows - Fills array from ListFills.
 */
function renderFills(rows) {
    const tbody = bySelector('#fills-table tbody');
    if (!tbody) return;
    empty(tbody);
    for (const r of rows) {
        const tr = document.createElement('tr');
        tr.innerHTML = `
      <td>${fmt(r.time)}</td>
      <td>${fmt(r.exec_id)}</td>
      <td>${fmt(r.symbol)}</td>
      <td>${fmt(r.side)}</td>
      <td>${fmt(r.filled_qty)}</td>
      <td>${fmt(r.price)}</td>
      <td>${fmt(r.order_id)}</td>
      <td>${fmt(r.commission || '')}</td>
      <td>${fmt(r.venue || 'SMART')}</td>
    `;
        tbody.appendChild(tr);
    }
}

/**
 * Render the Positions table.
 * @param {Array<Object>} rows - Positions from GetPositions.
 */
function renderPositions(rows) {
    const tbody = bySelector('#positions-table tbody');
    if (!tbody) return;
    empty(tbody);
    for (const r of rows) {
        const day = Number(r.day_pnl);
        const unrl = Number(r.unrealized_pnl);
        const tr = document.createElement('tr');
        tr.innerHTML = `
      <td>${fmt(r.symbol)}</td>
      <td>${fmt(r.position)}</td>
      <td>${fmt(r.avg_cost)}</td>
      <td>${fmt(r.market_price || '')}</td>
      <td class="${isNaN(day) ? '' : day >= 0 ? 'is-positive' : 'is-negative'}">${fmt(r.day_pnl || '')}</td>
      <td class="${isNaN(unrl) ? '' : unrl >= 0 ? 'is-positive' : 'is-negative'}">${fmt(r.unrealized_pnl || '')}</td>
    `;
        tbody.appendChild(tr);
    }
}

/**
 * Render the Account Values table.
 * @param {Array<Object>} rows - Account values from GetAccountValues.
 */
function renderAccountValues(rows) {
    const tbody = bySelector('#account-values-table tbody');
    if (!tbody) return;
    empty(tbody);

    // Prefer a few key metrics first if available, then append the rest.
    const pick = ['NetLiq', 'BuyingPower', 'MaintMarginReq', 'CashBalance'];
    const map = new Map(rows.map((x) => [x.tag, x]));
    const ordered = [];
    for (const k of pick)
        if (map.has(k)) ordered.push(map.get(k));
    for (const r of rows)
        if (!pick.includes(r.tag)) ordered.push(r);

    for (const r of ordered) {
        const tr = document.createElement('tr');
        tr.innerHTML = `<td>${fmt(r.tag)}</td><td>${fmt(r.value)}</td>`;
        tbody.appendChild(tr);
    }
}

// --- Polling & Events

/**
 * Start periodic polling for orders/fills and portfolio panels.
 */
function startPolling() {
    stopPolling();
    pollTimerOrders = setInterval(refreshOrdersAndFills, POLL_MS_ORDERS);
    pollTimerPortfolio = setInterval(refreshPortfolio, POLL_MS_PORTFOLIO);
}

/**
 * Stop any active polling timers.
 */
function stopPolling() {
    if (pollTimerOrders) {
        clearInterval(pollTimerOrders);
        pollTimerOrders = null;
    }
    if (pollTimerPortfolio) {
        clearInterval(pollTimerPortfolio);
        pollTimerPortfolio = null;
    }
}

/**
 * Wire DOM event listeners for interactive controls.
 */
function wireEvents() {
    const reconnectBtn = $('server-connect-button');
    if (reconnectBtn) {
        reconnectBtn.addEventListener('click', () => {
            const ep = prompt('Enter gRPC-web endpoint', grpcEndpoint) || grpcEndpoint;
            initGrpc(ep);
        });
    }

    const placeBtn = $('place-order-button');
    if (placeBtn) placeBtn.addEventListener('click', placeManualOrder);

    const cancelSel = $('orders-cancel-selected-button');
    if (cancelSel)
        cancelSel.addEventListener('click', () =>
            addNotification('Cancel selected: not yet implemented', 'warn')
        );

    const cancelAll = $('orders-cancel-all-button');
    if (cancelAll)
        cancelAll.addEventListener('click', () =>
            addNotification('Cancel all: not yet implemented', 'warn')
        );

    const modifyBtn = $('orders-modify-button');
    if (modifyBtn)
        modifyBtn.addEventListener('click', () =>
            addNotification('Modify: not yet implemented', 'warn')
        );

    const algoStop = $('algo-stop-button');
    if (algoStop)
        algoStop.addEventListener('click', () => {
            addNotification('Algo stop requested (stub)', 'warn');
            const badge = $('algo-state-badge');
            if (badge) {
                badge.textContent = 'STOPPING';
                badge.classList.remove('badge--success');
            }
        });
}

document.addEventListener('DOMContentLoaded', () => {
    wireEvents();
    initGrpc(DEFAULT_GRPC_ENDPOINT);
    startPolling();
});

// Console helper for manual testing
// TODO: Remove in prod
// window.tradrDebug = { setEndpoint: initGrpc, refreshOrdersAndFills, refreshPortfolio };
