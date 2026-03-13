/**
 * api.js - 주간 뉴스 대시보드 API 클라이언트 유틸리티
 */

const API_BASE = '';  // same origin

/**
 * GET 요청
 * @param {string} url - API endpoint (예: /api/history/recent)
 * @returns {Promise<Object>}
 */
async function apiGet(url) {
  const res = await fetch(API_BASE + url);
  if (!res.ok) {
    const body = await res.text();
    let message;
    try {
      const json = JSON.parse(body);
      message = json.detail || json.message || body;
    } catch {
      message = body;
    }
    throw new Error(`요청 실패 (${res.status}): ${message}`);
  }
  return res.json();
}

/**
 * POST 요청
 * @param {string} url - API endpoint
 * @param {Object} [body] - JSON 요청 본문
 * @returns {Promise<Object>}
 */
async function apiPost(url, body) {
  const options = {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
  };
  if (body !== undefined) {
    options.body = JSON.stringify(body);
  }
  const res = await fetch(API_BASE + url, options);
  if (!res.ok) {
    const text = await res.text();
    let message;
    try {
      const json = JSON.parse(text);
      message = json.detail || json.message || text;
    } catch {
      message = text;
    }
    throw new Error(`요청 실패 (${res.status}): ${message}`);
  }
  return res.json();
}

/**
 * SSE (Server-Sent Events) 연결
 * @param {string} url - SSE endpoint
 * @param {function} onProgress - progress 이벤트 콜백 ({step, current, total, message})
 * @param {function} onComplete - complete 이벤트 콜백 ({status})
 * @param {function} [onError] - 에러 콜백
 * @returns {EventSource}
 */
function connectSSE(url, onProgress, onComplete, onError) {
  const es = new EventSource(API_BASE + url);

  es.addEventListener('progress', (event) => {
    try {
      const data = JSON.parse(event.data);
      onProgress(data);
    } catch (e) {
      console.error('SSE progress 파싱 오류:', e);
    }
  });

  es.addEventListener('complete', (event) => {
    try {
      const data = JSON.parse(event.data);
      onComplete(data);
    } catch (e) {
      console.error('SSE complete 파싱 오류:', e);
    }
    es.close();
  });

  es.addEventListener('error_event', (event) => {
    try {
      const data = JSON.parse(event.data);
      if (onError) onError(new Error(data.message || data.error || '서버 오류가 발생했습니다.'));
    } catch {
      if (onError) onError(new Error('서버 오류가 발생했습니다.'));
    }
    es.close();
  });

  es.onerror = (event) => {
    // EventSource auto-reconnects on network errors. If readyState is CLOSED,
    // the server intentionally ended the stream.
    if (es.readyState === EventSource.CLOSED) {
      if (onError) onError(new Error('서버 연결이 종료되었습니다.'));
    }
  };

  return es;
}

/**
 * PATCH 요청
 * @param {string} url - API endpoint
 * @param {Object} [body] - JSON 요청 본문
 * @returns {Promise<Object>}
 */
async function apiPatch(url, body) {
  const options = {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
  };
  if (body !== undefined) {
    options.body = JSON.stringify(body);
  }
  const res = await fetch(API_BASE + url, options);
  if (!res.ok) {
    const text = await res.text();
    let message;
    try {
      const json = JSON.parse(text);
      message = json.detail || json.message || text;
    } catch {
      message = text;
    }
    throw new Error(`요청 실패 (${res.status}): ${message}`);
  }
  return res.json();
}

/* ---------- Toast Notifications ---------- */

const TOAST_ICONS = {
  success: '\u2713',
  error: '\u2717',
  info: '\u2139',
};

function showToast(message, type = 'info', duration = 3500) {
  let container = document.querySelector('.toast-container');
  if (!container) {
    container = document.createElement('div');
    container.className = 'toast-container';
    document.body.appendChild(container);
  }
  const toast = document.createElement('div');
  toast.className = `toast toast--${type}`;

  const icon = document.createElement('span');
  icon.className = 'toast__icon';
  icon.textContent = TOAST_ICONS[type] || TOAST_ICONS.info;

  const msg = document.createElement('span');
  msg.className = 'toast__msg';
  msg.textContent = message;

  const close = document.createElement('button');
  close.className = 'toast__close';
  close.innerHTML = '&times;';
  close.addEventListener('click', () => {
    toast.style.opacity = '0';
    toast.style.transition = 'opacity 0.2s ease';
    setTimeout(() => toast.remove(), 200);
  });

  toast.appendChild(icon);
  toast.appendChild(msg);
  toast.appendChild(close);
  container.appendChild(toast);

  setTimeout(() => {
    if (toast.parentNode) {
      toast.style.opacity = '0';
      toast.style.transition = 'opacity 0.3s ease';
      setTimeout(() => toast.remove(), 300);
    }
  }, duration);
}

/* ---------- Status Badge Helper ---------- */

function statusBadgeClass(status) {
  switch (status) {
    case '생성중': return 'badge--generating';
    case '미리보기': return 'badge--preview';
    case '업로드완료': return 'badge--uploaded';
    default: return '';
  }
}

function scoreBadgeClass(score) {
  if (score >= 70) return 'score-badge--high';
  if (score >= 40) return 'score-badge--mid';
  return 'score-badge--low';
}

/* ---------- Date Formatting ---------- */

function formatDate(isoStr) {
  if (!isoStr) return '-';
  const d = new Date(isoStr);
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

function formatWeekRange(weekRange) {
  if (!weekRange || weekRange.length < 2) return '-';
  return `${formatDate(weekRange[0])} ~ ${formatDate(weekRange[1])}`;
}

/**
 * 주차 레이블 생성 (n월 m주차)
 * week_range의 Monday 기준으로 계산
 */
function formatWeekLabel(weekRange) {
  if (!weekRange || weekRange.length < 2) return '-';
  const monday = new Date(weekRange[0] + 'T00:00:00');
  if (isNaN(monday.getTime())) return formatWeekRange(weekRange);
  const weekOfMonth = Math.floor((monday.getDate() - 1) / 7) + 1;
  return `${monday.getMonth() + 1}월 ${weekOfMonth}주차`;
}

/* ---------- Flatpickr 한국어 기본 설정 ---------- */
if (typeof flatpickr !== 'undefined' && flatpickr.l10ns && flatpickr.l10ns.ko) {
  flatpickr.localize(flatpickr.l10ns.ko);
}

/* ---------- TOAST UI Grid 공유 렌더러 ---------- */

/** 상태 뱃지 렌더러 (dashboard, log 페이지용) */
class StatusBadgeRenderer {
  constructor(props) {
    const el = document.createElement('span');
    el.className = 'badge ' + statusBadgeClass(props.value);
    el.textContent = props.value;
    this.el = el;
  }
  getElement() { return this.el; }
  render(props) {
    this.el.className = 'badge ' + statusBadgeClass(props.value);
    this.el.textContent = props.value;
  }
}

/**
 * 액션 버튼 렌더러 팩토리
 * @param {string} label - 버튼 텍스트
 * @param {function} onClick - 클릭 콜백 (rowKey, grid)
 */
function createActionButtonRenderer(label, onClick) {
  return class ActionButtonRenderer {
    constructor(props) {
      const btn = document.createElement('button');
      btn.className = 'btn btn--outline btn--sm';
      btn.textContent = label;
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        onClick(props.rowKey, props.grid);
      });
      this.el = btn;
    }
    getElement() { return this.el; }
    render() {}
  };
}

/** 공지 제목 링크 렌더러 (notices 페이지용) */
class NoticeTitleRenderer {
  constructor(props) {
    const el = document.createElement('a');
    const url = props.grid.getValue(props.rowKey, 'url');
    el.href = url || '#';
    el.target = '_blank';
    el.rel = 'noopener';
    el.textContent = props.value;
    el.style.cssText = 'color: var(--color-primary); text-decoration: none;';
    el.addEventListener('mouseover', () => { el.style.textDecoration = 'underline'; });
    el.addEventListener('mouseout', () => { el.style.textDecoration = 'none'; });
    this.el = el;
  }
  getElement() { return this.el; }
  render(props) {
    this.el.textContent = props.value;
    const url = props.grid.getValue(props.rowKey, 'url');
    this.el.href = url || '#';
  }
}

/** HTML 이스케이프 (공용) */
function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}
