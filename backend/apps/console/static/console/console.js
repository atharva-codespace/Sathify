/*
 * The Superadmin console.
 *
 * ---------------------------------------------------------------------------
 * WHY THERE IS NO FRAMEWORK HERE
 * ---------------------------------------------------------------------------
 * `docs/free-tier-constraints.md` describes a project with one free web service
 * and no room for a second. There is no Node toolchain anywhere in this repo,
 * so a console built with React would need a build pipeline nobody has a place
 * to run, and would be a console that could not be deployed.
 *
 * So: plain modules, hash routing, `fetch`, and the browser's own DOM. Served
 * as a Django template through WhiteNoise, exactly like every other static
 * asset the API already ships.
 *
 * ---------------------------------------------------------------------------
 * IT INVENTS NOTHING
 * ---------------------------------------------------------------------------
 * Every call below hits an endpoint that already exists under
 * `/api/v1/console/` (see `apps/console/urls.py`) or `/api/v1/auth/`. Where the
 * API has no field for something, the screen does without it rather than
 * guessing — a console that displays a number the server never sent is a
 * console that will eventually display a wrong one.
 */

'use strict';

const API = '/api/v1';
const TOKEN_KEY = 'sathify.console.access';
const REFRESH_KEY = 'sathify.console.refresh';

// ---------------------------------------------------------------------------
// Transport
// ---------------------------------------------------------------------------

const store = {
  get access() { return localStorage.getItem(TOKEN_KEY); },
  get refresh() { return localStorage.getItem(REFRESH_KEY); },
  set(access, refresh) {
    localStorage.setItem(TOKEN_KEY, access);
    if (refresh) localStorage.setItem(REFRESH_KEY, refresh);
  },
  clear() { localStorage.removeItem(TOKEN_KEY); localStorage.removeItem(REFRESH_KEY); },
};

class ApiError extends Error {
  constructor(message, status) { super(message); this.status = status; }
}

/**
 * One request, with a single silent retry after refreshing the token.
 *
 * Bounded to one retry on purpose: the API rotates refresh tokens, so a storm
 * of parallel refreshes would invalidate each other and sign the operator out
 * mid-reconciliation.
 */
async function request(path, { method = 'GET', body, query, raw = false, reason } = {}) {
  const url = new URL(API + path, location.origin);
  Object.entries(query || {}).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== '') url.searchParams.set(k, v);
  });

  const send = () => fetch(url, {
    method,
    headers: {
      'Content-Type': 'application/json',
      ...(store.access ? { Authorization: `Bearer ${store.access}` } : {}),
      // Read by `core.platform.PlatformScopedQuerysetMixin` and written into
      // the society's own access log, so an operator's stated purpose reaches
      // the people it concerns.
      ...(reason ? { 'X-Access-Reason': reason } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });

  let response = await send();
  if (response.status === 401 && store.refresh) {
    const refreshed = await fetch(`${API}/auth/refresh/`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh: store.refresh }),
    });
    if (refreshed.ok) {
      const data = await refreshed.json();
      store.set(data.access, data.refresh);
      response = await send();
    }
  }

  if (response.status === 401) { signOut(); throw new ApiError('Session expired.', 401); }
  if (raw) {
    if (!response.ok) throw new ApiError('Download failed.', response.status);
    return response;
  }
  const text = await response.text();
  const data = text ? JSON.parse(text) : null;
  if (!response.ok) {
    throw new ApiError(
      data?.detail || firstFieldError(data) || `Request failed (${response.status}).`,
      response.status,
    );
  }
  return data;
}

function firstFieldError(data) {
  if (!data || typeof data !== 'object') return null;
  const [, value] = Object.entries(data)[0] || [];
  return Array.isArray(value) ? value[0] : (typeof value === 'string' ? value : null);
}

// ---------------------------------------------------------------------------
// DOM helpers
// ---------------------------------------------------------------------------

const $ = (sel, root = document) => root.querySelector(sel);

/** Builds an element. Text is always set via textContent — never innerHTML —
 *  so a society name or a reason string cannot inject markup into the console. */
function el(tag, props = {}, children = []) {
  const node = document.createElement(tag);
  Object.entries(props).forEach(([key, value]) => {
    if (value === undefined || value === null || value === false) return;
    if (key === 'class') node.className = value;
    else if (key === 'text') node.textContent = value;
    else if (key === 'html') node.innerHTML = value;
    else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? '' : value);
  });
  (Array.isArray(children) ? children : [children])
    .filter(Boolean)
    .forEach((child) => node.append(child));
  return node;
}

const money = (m) => (m && m.display) ? m.display : '—';
const paise = (n) => `₹${((n || 0) / 100).toLocaleString('en-IN', { minimumFractionDigits: 2 })}`;
const date = (iso) => (iso ? new Date(iso).toLocaleDateString('en-IN', { day: '2-digit', month: 'short' }) : '—');
const dateTime = (iso) => (iso ? new Date(iso).toLocaleString('en-IN', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' }) : '—');

function toast(message, bad = false) {
  const node = el('div', { class: `toast${bad ? ' bad' : ''}`, text: message });
  document.body.append(node);
  setTimeout(() => node.remove(), 3200);
}

/** Loading, empty and error are first-class here rather than afterthoughts:
 *  every one of these screens can legitimately be slow, blank, or refused. */
const skeleton = (rows = 6) =>
  el('div', { class: 'skeleton' }, Array.from({ length: rows }, () => el('div')));

const emptyState = (title, message) =>
  el('div', { class: 'state' }, [el('h3', { text: title }), el('p', { text: message })]);

const errorState = (message, onRetry) =>
  el('div', { class: 'state' }, [
    el('h3', { text: 'Could not load this' }),
    el('p', { text: message }),
    onRetry ? el('button', { class: 'btn', text: 'Try again', onclick: onRetry }) : null,
  ]);

function drawer(title, children) {
  close();
  const backdrop = el('div', { class: 'drawer-backdrop', onclick: close });
  const panel = el('div', { class: 'drawer', role: 'dialog', 'aria-label': title }, [
    el('button', { class: 'link-btn', text: 'Close', onclick: close }),
    el('h2', { text: title }),
    ...children,
  ]);
  document.body.append(backdrop, panel);
  document.addEventListener('keydown', onEsc);
  function onEsc(event) { if (event.key === 'Escape') close(); }
  function close() {
    document.querySelectorAll('.drawer, .drawer-backdrop').forEach((n) => n.remove());
    document.removeEventListener('keydown', onEsc);
  }
  return close;
}

function row(label, value) {
  return el('div', { class: 'row' }, [el('span', { text: label }), el('span', { text: value ?? '—' })]);
}

function table(columns, rows) {
  return el('div', { class: 'table-wrap' }, [
    el('table', {}, [
      el('thead', {}, el('tr', {}, columns.map((c) =>
        el('th', { class: c.num ? 'num' : null, text: c.label })))),
      el('tbody', {}, rows),
    ]),
  ]);
}

/** Renders a screen through its lifecycle so no screen has to hand-roll it. */
async function render(target, load, draw) {
  target.replaceChildren(skeleton());
  try {
    const data = await load();
    target.replaceChildren(...[].concat(draw(data)));
  } catch (error) {
    target.replaceChildren(errorState(error.message, () => render(target, load, draw)));
  }
}

// ---------------------------------------------------------------------------
// Screens
// ---------------------------------------------------------------------------

const screens = {};

screens.overview = {
  title: 'Overview',
  render: (host) => render(host, () => request('/console/overview/'), (data) => {
    const tiles = el('div', { class: 'tiles' }, [
      tile('Platform revenue', paise(data.revenue.mrr_paise) + ' /mo', 'subscriptions + fees'),
      tile('GMV settled', paise(data.gmv.settled_paise), 'wages resident → worker'),
      tile('Societies', String(data.societies.active), `${data.societies.paid} paid · ${data.societies.suspended} suspended`),
      tile('Workers paid', String(data.workers.paid), `${data.workers.awaiting_payment} awaiting`),
      el('div', { class: 'not-additive', text: '↑ these two are never added together — Sathify earns nothing on GMV' }),
    ]);

    const queue = data.needs_attention.length
      ? el('div', { class: 'queue' }, data.needs_attention.map((item) =>
          el('div', { class: `queue-row ${item.severity}` }, [
            el('span', { class: `chip ${item.severity === 'critical' ? 'crit' : 'warn'}`, text: item.severity }),
            el('div', {}, [
              el('div', { text: item.label }),
              el('div', { class: 'small muted', text: describeQueueItem(item) }),
            ]),
            el('span', { class: 'mono', text: String(item.count) }),
          ])))
      : emptyState('Nothing needs attention', 'No stuck payments, no lapsed reviews, no held wages.');

    const integrity = data.billing_integrity;
    return [
      el('h1', { text: 'Overview' }),
      tiles,
      el('h2', { text: `Needs attention (${data.needs_attention.length})` }),
      queue,
      el('h2', { text: 'Billing integrity' }),
      el('div', { class: 'card' }, [
        el('p', { class: 'small muted', text: 'Whether wage figures on this platform rest on an observation or an inference. Below 90% tier-1/2 capture, hourly billing should not be enabled.' }),
        integrity.sessions
          ? el('div', {}, [
              row('Sessions (30d)', String(integrity.sessions)),
              row('Trusted capture', pct(integrity.trusted_capture_rate)),
              row('Auto-closed', pct(integrity.auto_close_rate)),
              row('Flagged', pct(integrity.flagged_rate)),
              el('div', { class: 'row' }, [
                el('span', { text: 'Hourly billing' }),
                el('span', { class: `chip ${integrity.hourly_billing_advised ? 'ok' : 'warn'}`,
                  text: integrity.hourly_billing_advised ? 'advised' : 'not advised' }),
              ]),
            ])
          : el('p', { class: 'muted', text: 'No work sessions recorded yet. No sessions is not a pass.' }),
      ]),
    ];
  }),
};

function describeQueueItem(item) {
  const parts = [];
  if (item.amount_paise) parts.push(paise(item.amount_paise));
  if (item.societies) parts.push(`${item.societies} societies`);
  if (item.detail) parts.push(item.detail.map((d) => `${d.society} ${d.workers}/${d.cap}`).join(', '));
  return parts.join(' · ');
}

const pct = (value) => (value === null || value === undefined ? '—' : `${Math.round(value * 100)}%`);

function tile(label, value, sub) {
  return el('div', { class: 'card tile' }, [
    el('div', { class: 'eyebrow', text: label }),
    el('div', { class: 'value', text: value }),
    el('div', { class: 'label', text: sub }),
  ]);
}

screens.transactions = {
  title: 'Transactions',
  render: (host) => {
    const state = { unsigned: '', status: '', kind: '' };
    const body = el('div');

    const filters = el('div', { class: 'filters' }, [
      select('Saved view', [['', 'All payments'], ['unsigned', 'Unsigned settlements *'], ['webhook_gap', 'Webhook gaps']],
        (value) => { state.unsigned = value; load(); }),
      select('Status', [['', 'Any status'], ['created', 'Created'], ['pending', 'Pending'], ['paid', 'Paid'], ['failed', 'Failed'], ['refunded', 'Refunded']],
        (value) => { state.status = value; load(); }),
      select('Kind', [['', 'Any kind'], ['engagement_salary', 'Salary'], ['booking', 'Booking'], ['emergency_surcharge', 'Emergency fee']],
        (value) => { state.kind = value; load(); }),
      el('span', { class: 'small muted', text: '* rests on a person’s word, not a signature' }),
    ]);

    function load() {
      render(body, () => request('/console/transactions/', {
        query: {
          [state.unsigned || 'ignore']: state.unsigned ? 'true' : undefined,
          status: state.status, kind: state.kind, search: searchTerm(),
        },
      }), (data) => {
        if (!data.count) return emptyState('No payments match', 'Try a wider filter or clear the search.');
        return table(
          [{ label: 'Receipt' }, { label: 'Date' }, { label: 'Society' }, { label: 'Resident → Worker' },
           { label: 'Kind' }, { label: 'Amount', num: true }, { label: 'Status' }, { label: 'Via' }],
          data.results.map((p) => el('tr', { class: 'clickable', onclick: () => openPayment(p.receipt_number) }, [
            el('td', { class: 'mono', text: p.receipt_number }),
            el('td', { text: date(p.created_at) }),
            el('td', { text: p.society_name }),
            el('td', { text: `${p.resident_label || '—'} → ${p.worker_label}` }),
            el('td', { text: p.kind }),
            el('td', { class: 'num', text: money(p.amount) }),
            el('td', {}, el('span', { class: `chip ${statusTone(p.status)}`, text: p.status })),
            el('td', {}, p.rests_on_a_person
              ? el('span', { class: 'chip warn', text: 'UTR *' })
              : el('span', { class: 'chip', text: p.settled_via || '—' })),
          ])));
      });
    }

    host.replaceChildren(el('h1', { text: 'Transactions' }), filters, body);
    load();
    onSearch(load);
  },
};

const statusTone = (status) => ({ paid: 'ok', failed: 'crit', refunded: 'warn', pending: 'info' }[status] || '');

async function openPayment(receipt) {
  try {
    const p = await request(`/console/transactions/${encodeURIComponent(receipt)}/`);
    const evidence = p.settlement_evidence || {};
    drawer(`${p.receipt_number} · ${money(p.total)}`, [
      row('Status', p.status),
      row('Society', p.society_name),
      row('Resident → Worker', `${p.resident_label || '—'} → ${p.worker_label}`),
      row('Period', `${p.period_start || '—'} – ${p.period_end || '—'}`),
      row('Platform fee', money(p.platform_fee)),
      row('Worker receives', money(p.worker_receives)),
      evidence.kind === 'assertion'
        ? el('div', { class: 'assertion' }, [
            el('strong', { text: 'No gateway signature.' }),
            el('p', { class: 'small', text: evidence.warning || '' }),
            row('UTR', evidence.utr),
            row('Amount seen', evidence.amount_seen),
            row('Confirmed by', evidence.confirmed_by),
          ])
        : el('p', { class: 'small muted', text: evidence.kind === 'signature'
            ? `Verified Razorpay signature (${evidence.razorpay_payment_id}).`
            : 'No settlement evidence recorded.' }),
      p.invoice ? el('div', {}, [
        el('h2', { text: 'Invoice' }),
        row('Number', p.invoice.number),
        row('Sessions', String(p.invoice.sessions)),
        row('Held', p.invoice.held),
      ]) : null,
    ]);
  } catch (error) { toast(error.message, true); }
}

screens.activity = {
  title: 'Activity',
  render: (host) => {
    const body = el('div');
    let tab = 'access-log';

    const tabs = el('div', { class: 'filters' }, [
      tabButton('Access log', 'access-log'), tabButton('Impersonations', 'impersonations'), tabButton('Work sessions', 'sessions'),
    ]);
    function tabButton(label, key) {
      return el('button', {
        class: 'btn small', text: label,
        onclick: (event) => {
          tab = key;
          tabs.querySelectorAll('.btn').forEach((b) => b.classList.remove('primary'));
          event.target.classList.add('primary');
          load();
        },
      });
    }
    tabs.querySelector('.btn').classList.add('primary');

    function load() {
      const path = { 'access-log': '/console/activity/access-log/', impersonations: '/console/activity/impersonations/', sessions: '/console/activity/sessions/' }[tab];
      render(body, () => request(path), (data) => {
        if (!data.count) return emptyState('Nothing recorded', 'This log is empty for the current filters.');
        if (tab === 'impersonations') {
          return table([{ label: 'Started' }, { label: 'Operator' }, { label: 'Acting as' }, { label: 'Society' }, { label: 'Reason' }, { label: 'State' }],
            data.results.map((g) => el('tr', {}, [
              el('td', { text: dateTime(g.started_at) }),
              el('td', { text: g.superadmin_name }),
              el('td', { text: g.target_name }),
              el('td', { text: g.society_name }),
              // Reasons render inline, never behind a hover: an audit trail
              // whose justification takes a click is one nobody reads.
              el('td', { text: g.reason }),
              el('td', {}, el('span', { class: `chip ${g.is_live ? 'info' : ''}`, text: g.is_live ? 'live' : 'ended' })),
            ])));
        }
        if (tab === 'sessions') {
          return table([{ label: 'Date' }, { label: 'Society' }, { label: 'Worker' }, { label: 'Tier' }, { label: 'Status' }, { label: 'Billed', num: true }],
            data.results.map((s) => el('tr', {}, [
              el('td', { text: s.visit_date }),
              el('td', { text: s.society_name }),
              el('td', { text: s.worker_name }),
              el('td', {}, el('span', { class: `chip ${s.tier <= 2 ? 'ok' : 'warn'}`, text: `T${s.tier}` })),
              el('td', { text: s.status }),
              el('td', { class: 'num', text: money(s.total) }),
            ])));
        }
        return table([{ label: 'When' }, { label: 'Operator' }, { label: 'Society' }, { label: 'Record' }, { label: 'Action' }, { label: 'Reason' }, { label: 'Rows', num: true }],
          data.results.map((a) => el('tr', {}, [
            el('td', { text: dateTime(a.created_at) }),
            el('td', { text: a.superadmin_name }),
            el('td', { text: a.society_name }),
            el('td', { class: 'mono', text: a.model_label }),
            el('td', { text: a.action }),
            el('td', { text: a.reason || '—' }),
            el('td', { class: 'num', text: String(a.row_count) }),
          ])));
      });
    }

    host.replaceChildren(
      el('h1', { text: 'Activity' }),
      el('p', { class: 'small muted', text: 'Every cross-society read of a person’s record is logged here, and the society it concerns can read its own rows.' }),
      tabs, body,
    );
    load();
  },
};

screens.societies = {
  title: 'Societies',
  render: (host) => {
    const body = el('div');
    function load() {
      render(body, () => request('/console/societies/', { query: { search: searchTerm() } }), (data) => {
        if (!data.count) return emptyState('No societies', 'Nothing matches that search.');
        return table([{ label: 'Name' }, { label: 'City' }, { label: 'Flats', num: true }, { label: 'Workers', num: true }, { label: 'Tier' }, { label: 'Status' }],
          data.results.map((s) => el('tr', { class: 'clickable', onclick: () => openSociety(s.id, load) }, [
            el('td', { text: s.name }),
            el('td', { text: s.city }),
            el('td', { class: 'num', text: String(s.total_flats) }),
            el('td', { class: 'num' }, el('span', { class: s.over_cap ? 'chip crit' : '', text: `${s.workers}${s.worker_cap ? ` / ${s.worker_cap}` : ''}` })),
            el('td', {}, el('span', { class: 'chip', text: s.tier })),
            el('td', {}, el('span', { class: `chip ${s.status === 'active' ? 'ok' : 'warn'}`, text: s.status })),
          ])));
      });
    }
    host.replaceChildren(el('h1', { text: 'Societies' }), body);
    load();
    onSearch(load);
  },
};

async function openSociety(id, reload) {
  try {
    const s = await request(`/console/societies/${id}/`);
    const scope = s.suspension_scope || {};
    const close = drawer(s.name, [
      row('Status', s.status),
      row('Tier', s.tier),
      row('Workers', `${s.workers}${s.worker_cap ? ` of ${s.worker_cap}` : ' (no cap)'}`),
      row('Gates', String(s.gates)),
      row('Visit overhead', `${s.billing.visit_overhead_minutes} min`),
      row('Rounding', `${s.billing.round_minutes} min${s.billing.round_up_in_workers_favour ? ', up' : ', nearest'}`),
      el('h2', { text: 'Subscription' }),
      el('div', { class: 'filters' }, [
        ...['free', 'standard', 'plus'].map((tier) => el('button', {
          class: `btn small${tier === s.tier ? ' primary' : ''}`,
          text: tier,
          onclick: () => changeTier(id, tier, close, reload),
        })),
      ]),
      el('p', { class: 'small muted', text: 'A lapsed paid tier reads as free rather than as itself, so a society is never locked out of its own records by a billing gap.' }),

      el('h2', { text: 'Suspension' }),
      // Restated before the button, not after. This is the console action most
      // likely to be believed to do more than it does.
      el('div', { class: 'assertion' }, [
        el('p', { class: 'small', text: `Stops: ${(scope.stops || []).join(', ')}.` }),
        el('p', { class: 'small', text: `Keeps working: ${(scope.keeps_working || []).join(', ')}.` }),
        el('p', { class: 'small muted', text: scope.why || '' }),
      ]),
      el('button', {
        class: 'btn danger', text: 'Suspend this society…',
        onclick: async () => {
          const reason = prompt('Why is this society being suspended? (recorded)');
          if (!reason || reason.trim().length < 10) return toast('A reason of at least 10 characters is required.', true);
          try {
            await request(`/console/societies/${id}/suspend/`, {
              method: 'POST',
              body: { reason: reason.trim(), acknowledge_gate_keeps_working: true },
            });
            toast('Suspended. Gate checks and attendance are unaffected.');
            close(); reload();
          } catch (error) { toast(error.message, true); }
        },
      }),
    ]);
  } catch (error) { toast(error.message, true); }
}

async function changeTier(id, tier, close, reload) {
  const reason = prompt(`Why is this society moving to ${tier}? (recorded)`);
  if (!reason || reason.trim().length < 3) return;
  try {
    const result = await request(`/console/societies/${id}/tier/`, {
      method: 'POST',
      body: { tier, reason: reason.trim() },
    });
    toast(`Now on ${result.effective_tier}.`);
    close(); reload();
  } catch (error) { toast(error.message, true); }
}

screens.users = {
  title: 'Users',
  render: (host) => {
    const body = el('div');
    const state = { role: '' };
    const filters = el('div', { class: 'filters' }, [
      select('Role', [['', 'Any role'], ['resident', 'Residents'], ['worker', 'Workers'], ['guard', 'Guards'], ['society_admin', 'Society admins']],
        (value) => { state.role = value; load(); }),
      el('span', { class: 'small muted', text: 'Phone numbers are masked. Revealing one is logged and needs a reason.' }),
    ]);

    function load() {
      render(body, () => request('/console/users/', {
        query: { role: state.role, search: searchTerm() },
        reason: 'console user directory',
      }), (data) => {
        if (!data.count) return emptyState('No users match', 'Try a different role or search.');
        return table([{ label: 'Name' }, { label: 'Role' }, { label: 'Phone' }, { label: 'Society' }, { label: 'State' }, { label: '' }],
          data.results.map((u) => el('tr', {}, [
            el('td', { text: u.name }),
            el('td', { text: u.role }),
            el('td', { class: 'mono', text: u.phone }),
            el('td', { text: u.society_name || '—' }),
            el('td', {}, el('span', { class: `chip ${u.is_approved ? 'ok' : 'warn'}`, text: u.is_approved ? 'approved' : 'pending' })),
            el('td', {}, el('button', { class: 'btn small', text: 'Reveal', onclick: () => reveal(u.id) })),
          ])));
      });
    }

    host.replaceChildren(el('h1', { text: 'Users' }), filters, body);
    load();
    onSearch(load);
  },
};

async function reveal(id) {
  const reason = prompt('Why do you need this contact detail? (recorded, and visible to their society)');
  if (!reason || reason.trim().length < 10) return toast('A reason of at least 10 characters is required.', true);
  try {
    const data = await request(`/console/users/${id}/reveal/`, { method: 'POST', body: { reason: reason.trim() } });
    drawer('Contact details', [
      row('Phone', data.phone_number),
      row('Email', data.email || '—'),
      el('p', { class: 'small muted', text: 'This reveal has been logged against their society.' }),
    ]);
  } catch (error) { toast(error.message, true); }
}

screens.reports = {
  title: 'Reports',
  render: (host) => {
    const body = el('div');

    // The builder mirrors what the API accepts and nothing more. `include_pii`
    // is the only field that can widen what leaves the platform, so it carries
    // its own reason box and its own warning.
    const form = el('form', { class: 'card', onsubmit: submit }, [
      el('div', { class: 'filters' }, [
        labelled('Report', select(null, [['attendance', 'Attendance'], ['payments', 'Payments'], ['complaints', 'Complaints']], null, 'kind')),
        labelled('Scope', select(null, [['all', 'Every society'], ['tier', 'By tier'], ['selected', 'Chosen societies']], null, 'scope')),
        labelled('Tier', select(null, [['', '—'], ['free', 'Free'], ['standard', 'Standard'], ['plus', 'Plus']], null, 'tier')),
        labelled('From', el('input', { type: 'date', name: 'period_start', required: true })),
        labelled('To', el('input', { type: 'date', name: 'period_end', required: true })),
      ]),
      el('div', { class: 'filters' }, [
        checkbox('csv', 'CSV', true), checkbox('pdf', 'PDF', false),
        el('label', { class: 'field' }, [
          el('span', { text: 'Include names (personal details)' }),
          el('input', { type: 'checkbox', name: 'include_pii' }),
        ]),
        el('input', { name: 'reason', placeholder: 'Why names are needed (required for PII)', size: 46 }),
      ]),
      el('button', { class: 'btn primary', type: 'submit', text: 'Queue report' }),
      el('p', { class: 'small muted', text: 'Cross-society builds run as a bounded sweep — there is no task queue on this plan. Loading this page advances any queued work.' }),
    ]);

    async function submit(event) {
      event.preventDefault();
      const data = new FormData(form);
      const formats = ['csv', 'pdf'].filter((f) => data.get(f));
      if (!formats.length) return toast('Choose at least one format.', true);
      try {
        await request('/console/reports/new/', {
          method: 'POST',
          body: {
            kind: data.get('kind'), scope: data.get('scope'), tier: data.get('tier') || '',
            period_start: data.get('period_start'), period_end: data.get('period_end'),
            formats, include_pii: !!data.get('include_pii'), reason: data.get('reason') || '',
          },
        });
        toast('Queued. It will build as the sweep runs.');
        load();
      } catch (error) { toast(error.message, true); }
    }

    function load() {
      render(body, () => request('/console/reports/'), (data) => {
        if (!data.count) return emptyState('No reports yet', 'Queue one above. Builds run in the background.');
        return table([{ label: 'Report' }, { label: 'Period' }, { label: 'Scope' }, { label: 'Progress' }, { label: 'Rows', num: true }, { label: 'State' }, { label: '' }],
          data.results.map((job) => el('tr', {}, [
            el('td', { text: job.kind }),
            el('td', { text: job.period_label }),
            el('td', { text: job.scope === 'tier' ? `tier: ${job.tier}` : job.scope }),
            el('td', {}, progressCell(job)),
            el('td', { class: 'num', text: String(job.row_count) }),
            el('td', {}, el('span', { class: `chip ${jobTone(job.status)}`, text: job.status })),
            el('td', {}, jobActions(job, load)),
          ])));
      });
    }

    host.replaceChildren(el('h1', { text: 'Reports' }), form, el('h2', { text: 'Recent jobs' }), body);
    load();
  },
};

const jobTone = (status) => ({ ready: 'ok', partial: 'warn', failed: 'crit', running: 'info' }[status] || '');

function progressCell(job) {
  const p = job.progress || { done: 0, total: 0, percent: 0, failed: 0 };
  return el('div', {}, [
    el('div', { class: 'bar' }, el('i', { style: `width:${p.percent}%` })),
    el('div', { class: 'small muted', text: `${p.done}/${p.total} societies${p.failed ? ` · ${p.failed} failed` : ''}` }),
    // Named, not counted. A report silently missing three societies invites
    // conclusions drawn from an incomplete total.
    job.failed_societies?.length
      ? el('div', { class: 'small', text: `Missing: ${job.failed_societies.map((f) => f.society_name).join(', ')}` })
      : null,
  ]);
}

function jobActions(job, reload) {
  const actions = [];
  if (job.is_downloadable) {
    (job.formats || []).forEach((fmt) => actions.push(
      el('button', { class: 'btn small', text: fmt.toUpperCase(), onclick: () => download(job.id, fmt) })));
  }
  if (job.can_retry) {
    actions.push(el('button', {
      class: 'btn small', text: 'Retry failed',
      onclick: async () => {
        try {
          await request(`/console/reports/${job.id}/retry/`, { method: 'POST' });
          toast('Re-queued the societies that failed.');
          reload();
        } catch (error) { toast(error.message, true); }
      },
    }));
  }
  if (job.last_error) actions.push(el('span', { class: 'small error', text: job.last_error }));
  return el('div', { class: 'filters' }, actions);
}

async function download(id, fmt) {
  try {
    const response = await request(`/console/reports/${id}/${fmt}/`, { raw: true });
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = el('a', { href: url, download: `sathify-report.${fmt}` });
    document.body.append(link); link.click(); link.remove();
    URL.revokeObjectURL(url);
  } catch (error) { toast(error.message, true); }
}

// ---------------------------------------------------------------------------
// Shared controls
// ---------------------------------------------------------------------------

function select(label, options, onChange, name) {
  const node = el('select', { name }, options.map(([value, text]) =>
    el('option', { value, text })));
  if (onChange) node.addEventListener('change', () => onChange(node.value));
  return label ? el('label', { class: 'field' }, [el('span', { text: label }), node]) : node;
}

function labelled(text, control) {
  return el('label', { class: 'field' }, [el('span', { text }), control]);
}

function checkbox(name, label, checked) {
  return el('label', { class: 'field' }, [
    el('span', { text: label }),
    el('input', { type: 'checkbox', name, checked: checked || undefined }),
  ]);
}

let searchHandler = null;
const searchTerm = () => $('#search').value.trim();
function onSearch(handler) {
  searchHandler = handler;
}
$('#search')?.addEventListener('input', debounce(() => searchHandler && searchHandler(), 350));

function debounce(fn, ms) {
  let timer;
  return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), ms); };
}

// ---------------------------------------------------------------------------
// Routing and session
// ---------------------------------------------------------------------------

const ORDER = ['overview', 'transactions', 'activity', 'reports', 'societies', 'users'];

function buildNav() {
  $('#nav').replaceChildren(...ORDER.map((key) =>
    el('button', {
      type: 'button', text: screens[key].title, 'data-screen': key,
      onclick: () => { location.hash = `#/${key}`; },
    })));
}

function route() {
  const key = (location.hash.replace('#/', '') || 'overview');
  const screen = screens[key] || screens.overview;
  searchHandler = null;
  $('#nav').querySelectorAll('button').forEach((b) =>
    b.toggleAttribute('aria-current', b.dataset.screen === key));
  screen.render($('#screen'));
}

async function boot() {
  if (!store.access) return showLogin();
  try {
    const me = await request('/auth/me/');
    if (me.role !== 'superadmin') {
      // A society admin's token is valid but this is not their surface. Saying
      // so beats an endless wall of 403s from every panel.
      signOut('This console is for platform operators. Society administrators use the app.');
      return;
    }
    $('#who').textContent = me.first_name ? `${me.first_name} ${me.last_name || ''}`.trim() : me.phone_number;
    $('#level').textContent = 'platform operator';
    $('#app').hidden = false;
    $('#login').hidden = true;
    buildNav();
    window.addEventListener('hashchange', route);
    route();
  } catch (error) {
    showLogin(error.status === 401 ? '' : error.message);
  }
}

function showLogin(message) {
  $('#app').hidden = true;
  $('#login').hidden = false;
  if (message) { const box = $('#login-error'); box.textContent = message; box.hidden = false; }
}

function signOut(message) {
  store.clear();
  showLogin(message);
}

$('#signout')?.addEventListener('click', () => signOut());

$('#login-form')?.addEventListener('submit', async (event) => {
  event.preventDefault();
  const button = $('#login-btn');
  const box = $('#login-error');
  box.hidden = true;
  button.disabled = true;
  button.textContent = 'Signing in…';
  try {
    const data = await request('/auth/login/', {
      method: 'POST',
      body: { phone_number: $('#phone').value.trim(), password: $('#password').value },
    });
    store.set(data.access, data.refresh);
    await boot();
  } catch (error) {
    box.textContent = error.message;
    box.hidden = false;
  } finally {
    button.disabled = false;
    button.textContent = 'Sign in';
  }
});

boot();
