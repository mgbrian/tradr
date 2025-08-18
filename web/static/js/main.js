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

// Persist selected orders across re-renders
const selectedOrderIds = new Set();

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
        s === 'SUBMITTED' || s === 'PENDING' || s === 'PENDING_SUBMIT' || s === 'ACKED' || s === 'CANCEL_REQUESTED' ?
        'badge--info' :
        s === 'CANCELLED' || s === 'REJECTED' || s === 'ERROR' ?
        '' :
        'badge--info';
    return `<span class="badge ${cls}">${fmt(status)}</span>`;
}

const FINAL_STATES = new Set(['FILLED', 'CANCELLED', 'REJECTED']);
function isFinalStatus(status) {
    return FINAL_STATES.has((status || '').toUpperCase());
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
 * Submit a manual stock order via PlaceStockOrder.
 * Supports MKT / LMT / STP with optional TIF (DAY/GTC).
 */
async function placeManualOrder() {
    if (!grpcClient) return;
    const symbol = $('order-symbol').value.trim().toUpperCase();
    const side = $('order-side').value;
    const qty = parseInt($('order-qty').value, 10) || 0;
    const type = $('order-type').value; // "MKT" | "LMT" | "STP"
    const tif = $('order-tif').value || 'DAY';

    // For LMT/STP only
    const priceRaw = $('order-limit-price').value;
    const limitPrice =
        priceRaw === '' || priceRaw === null || priceRaw === undefined
            ? null
            : Number(priceRaw);

    if (!symbol || !qty || qty <= 0) {
        addNotification('Enter a valid symbol and quantity', 'warn');
        return;
    }
    if ((type === 'LMT' || type === 'STP') && !(typeof limitPrice === 'number' && isFinite(limitPrice))) {
        addNotification(`${type} requires a valid price`, 'warn');
        return;
    }

    try {
        // Pass price only when needed to preserve proto optional semantics.
        const priceArg = (type === 'LMT' || type === 'STP') ? limitPrice : undefined;
        const resp = await grpcClient.PlaceStockOrder(
            symbol,
            side,
            qty,
            type,
            priceArg,
            tif
        );
        addNotification(
            `Order placed: #${resp.order_id} (${symbol} ${side} ${type} x${qty}${priceArg !== undefined ? ' @ ' + priceArg : ''}, ${tif})`,
            'info'
        );
        refreshOrdersAndFills();
    } catch (err) {
        console.error(err);
        addNotification(`Order failed: ${err && err.message ? err.message : err}`, 'error');
    }
}

// --- Cancellation helpers & handlers

function getSelectedOrderIds() {
    // Use the stable set (survives re-renders)
    return Array.from(selectedOrderIds);
}

function getAllCancellableOrderIds() {
    const boxes = document.querySelectorAll('#orders-table tbody input.order-select[type=checkbox]:not([disabled])');
    return Array.from(boxes).map(b => Number(b.dataset.orderId)).filter(Number.isFinite);
}

async function cancelSelectedOrders() {
    if (!grpcClient) return;
    const ids = getSelectedOrderIds();
    if (!ids.length) {
        addNotification('No orders selected', 'warn');
        return;
    }
    await cancelMany(ids, 'selected');
}

async function cancelAllOrders() {
    if (!grpcClient) return;
    const ids = getAllCancellableOrderIds();
    if (!ids.length) {
        addNotification('No cancellable orders found', 'warn');
        return;
    }
    await cancelMany(ids, 'all');
}

async function cancelMany(ids, label) {
    try {
        const results = await Promise.allSettled(ids.map(id => grpcClient.CancelOrder(id)));
        let ok = 0, skipped = 0, failed = 0;
        results.forEach((res, i) => {
            const id = ids[i];
            if (res.status === 'fulfilled') {
                const { ok: okFlag, status, message } = res.value || {};
                if (okFlag) {
                    ok += 1;
                    addNotification(`Cancel requested for #${id}${status ? ` (${status})` : ''}`, 'info');
                } else {
                    skipped += 1;
                    addNotification(`Cancel skipped for #${id}${message ? `: ${message}` : ''}`, 'warn');
                }
            } else {
                failed += 1;
                const reason = res.reason && res.reason.message ? res.reason.message : String(res.reason);
                addNotification(`Cancel failed for #${id}: ${reason}`, 'error');
            }
        });
        if (label) {
            addNotification(`Cancel ${label}: ${ok} requested, ${skipped} skipped, ${failed} failed`, failed ? 'warn' : 'info');
        }
        refreshOrdersAndFills();
    } catch (err) {
        console.error(err);
        addNotification(`Cancel ${label} failed: ${err && err.message ? err.message : err}`, 'error');
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

    // Track which ids are present to prune stale selections
    const presentIds = new Set();

    for (const r of rows) {
        const id = Number(r.order_id);
        presentIds.add(id);

        const final = isFinalStatus(r.status);
        // Ensure we don't keep selections for final orders
        if (final) selectedOrderIds.delete(id);

        const checked = selectedOrderIds.has(id) && !final;

        const tr = document.createElement('tr');
        tr.setAttribute('data-order-id', String(id));
        tr.setAttribute('data-status', String(r.status || ''));

        tr.innerHTML = `
      <td>
        <input
          type="checkbox"
          class="order-select"
          data-order-id="${String(id)}"
          ${final ? 'disabled' : ''}
          ${checked ? 'checked' : ''} />
      </td>
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

    // Prune selections for orders that disappeared from the table
    for (const selId of Array.from(selectedOrderIds)) {
        if (!presentIds.has(selId)) selectedOrderIds.delete(selId);
    }

    // Keep header "select all" state sane after re-render
    const selectAll = document.querySelector('#orders-table thead input[type=checkbox]');
    if (selectAll) {
        const boxes = tbody.querySelectorAll('input.order-select:not([disabled])');
        const allSelected = boxes.length > 0 && Array.from(boxes).every(b => b.checked);
        selectAll.checked = allSelected;
        // tristate isn't supported natively; could set selectAll.indeterminate when some selected
        selectAll.indeterminate = !allSelected && Array.from(boxes).some(b => b.checked);
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

    // Enable/disable price field depending on type selection.
    const typeSel = $('order-type');
    const priceInput = $('order-limit-price');
    if (typeSel && priceInput) {
        const togglePrice = () => {
            const t = typeSel.value;
            const wantsPrice = (t === 'LMT' || t === 'STP');
            priceInput.disabled = !wantsPrice;
            if (!wantsPrice) priceInput.value = '';
            priceInput.placeholder = wantsPrice ? 'e.g. 123.45' : '—';
        };
        typeSel.addEventListener('change', togglePrice);
        togglePrice();
    }

    // --- Cancellation buttons
    const cancelSel = $('orders-cancel-selected-button');
    if (cancelSel) cancelSel.addEventListener('click', cancelSelectedOrders);

    const cancelAll = $('orders-cancel-all-button');
    if (cancelAll) cancelAll.addEventListener('click', cancelAllOrders);

    const modifyBtn = $('orders-modify-button');
    if (modifyBtn) modifyBtn.addEventListener('click', () =>
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

    // Select-all checkbox for orders table
    const selectAll = document.querySelector('#orders-table thead input[type=checkbox]');
    if (selectAll) {
        selectAll.addEventListener('change', () => {
            const tbody = document.querySelector('#orders-table tbody');
            const boxes = document.querySelectorAll('#orders-table tbody input.order-select[type=checkbox]:not([disabled])');
            boxes.forEach(b => {
                b.checked = !!selectAll.checked;
                const id = Number(b.dataset.orderId);
                if (!Number.isFinite(id)) return;
                if (selectAll.checked) selectedOrderIds.add(id);
                else selectedOrderIds.delete(id);
            });
            // keep header state coherent if there are no boxes
            const any = boxes.length > 0;
            selectAll.indeterminate = false;
            selectAll.checked = any && !!selectAll.checked;
        });
    }

    // Row checkbox changes -> keep selection set + header in sync
    const tbody = document.querySelector('#orders-table tbody');
    if (tbody) {
        tbody.addEventListener('change', (e) => {
            const cb = e.target;
            if (!cb.matches('input.order-select[type=checkbox]')) return;
            const id = Number(cb.dataset.orderId);
            if (!Number.isFinite(id)) return;
            if (cb.checked) selectedOrderIds.add(id);
            else selectedOrderIds.delete(id);

            const head = document.querySelector('#orders-table thead input[type=checkbox]');
            if (head) {
                const boxes = tbody.querySelectorAll('input.order-select:not([disabled])');
                const allSelected = boxes.length > 0 && Array.from(boxes).every(b => b.checked);
                head.checked = allSelected;
                head.indeterminate = !allSelected && Array.from(boxes).some(b => b.checked);
            }
        });
    }
}

document.addEventListener('DOMContentLoaded', () => {
    wireEvents();
    initGrpc(DEFAULT_GRPC_ENDPOINT);
    startPolling();
});

// Console helper for manual testing
// TODO: Remove in prod
// window.tradrDebug = { setEndpoint: initGrpc, refreshOrdersAndFills, refreshPortfolio };
