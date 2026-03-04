/**
 * log.js - 전체 이력 페이지 로직
 */

(function () {
  'use strict';

  const PER_PAGE = 10;

  const filterForm = document.getElementById('filter-form');
  const dateFrom = document.getElementById('date-from');
  const dateTo = document.getElementById('date-to');
  const searchText = document.getElementById('search-text');
  const tbody = document.getElementById('log-tbody');
  const paginationEl = document.getElementById('pagination');

  const modal = document.getElementById('md-modal');
  const modalClose = document.getElementById('md-modal-close');
  const modalContent = document.getElementById('md-modal-content');

  let currentPage = 1;
  let totalPages = 1;

  // ------------------------------------------------------------------
  // Fetch & Render
  // ------------------------------------------------------------------

  async function loadHistory(page) {
    currentPage = page || 1;

    const params = new URLSearchParams();
    params.set('page', currentPage);
    params.set('per_page', PER_PAGE);
    if (dateFrom.value) params.set('date_from', dateFrom.value);
    if (dateTo.value) params.set('date_to', dateTo.value);
    if (searchText.value.trim()) params.set('search', searchText.value.trim());

    tbody.innerHTML = `<tr><td colspan="5" class="text-center" style="padding:32px"><div class="spinner"></div></td></tr>`;

    try {
      const data = await apiGet(`/api/history?${params.toString()}`);
      totalPages = Math.max(1, Math.ceil(data.total / PER_PAGE));
      renderTable(data.items || []);
      renderPagination();
    } catch (err) {
      tbody.innerHTML = `<tr><td colspan="5" class="text-center" style="padding:32px;color:var(--color-text-secondary);">이력을 불러올 수 없습니다.</td></tr>`;
      showToast(err.message, 'error');
    }
  }

  function renderTable(items) {
    if (items.length === 0) {
      tbody.innerHTML = `<tr><td colspan="5" class="text-center" style="padding:32px;color:var(--color-text-secondary);">검색 결과가 없습니다.</td></tr>`;
      paginationEl.innerHTML = '';
      return;
    }

    tbody.innerHTML = items.map((item) => `
      <tr>
        <td>${formatWeekRange(item.week_range)}</td>
        <td>${item.article_count}건</td>
        <td><span class="badge ${statusBadgeClass(item.status)}">${item.status}</span></td>
        <td>${formatDate(item.created_at)}</td>
        <td>
          <button class="btn btn--secondary btn--sm" data-view-id="${item.id}">조회</button>
        </td>
      </tr>
    `).join('');

    tbody.querySelectorAll('[data-view-id]').forEach((btn) => {
      btn.addEventListener('click', () => openMarkdown(btn.dataset.viewId));
    });
  }

  // ------------------------------------------------------------------
  // Pagination
  // ------------------------------------------------------------------

  function renderPagination() {
    if (totalPages <= 1) {
      paginationEl.innerHTML = '';
      return;
    }

    let html = '';

    // Previous
    html += `<button class="pagination__btn" data-page="${currentPage - 1}" ${currentPage <= 1 ? 'disabled' : ''}>이전</button>`;

    // Page numbers - show a window of pages around current
    const maxVisible = 5;
    let start = Math.max(1, currentPage - Math.floor(maxVisible / 2));
    let end = Math.min(totalPages, start + maxVisible - 1);
    if (end - start + 1 < maxVisible) {
      start = Math.max(1, end - maxVisible + 1);
    }

    if (start > 1) {
      html += `<button class="pagination__btn" data-page="1">1</button>`;
      if (start > 2) html += `<span style="padding:0 4px;color:var(--color-text-muted);">...</span>`;
    }

    for (let i = start; i <= end; i++) {
      html += `<button class="pagination__btn ${i === currentPage ? 'active' : ''}" data-page="${i}">${i}</button>`;
    }

    if (end < totalPages) {
      if (end < totalPages - 1) html += `<span style="padding:0 4px;color:var(--color-text-muted);">...</span>`;
      html += `<button class="pagination__btn" data-page="${totalPages}">${totalPages}</button>`;
    }

    // Next
    html += `<button class="pagination__btn" data-page="${currentPage + 1}" ${currentPage >= totalPages ? 'disabled' : ''}>다음</button>`;

    paginationEl.innerHTML = html;

    paginationEl.querySelectorAll('[data-page]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const page = parseInt(btn.dataset.page, 10);
        if (page >= 1 && page <= totalPages) loadHistory(page);
      });
    });
  }

  // ------------------------------------------------------------------
  // Markdown modal
  // ------------------------------------------------------------------

  async function openMarkdown(historyId) {
    modal.classList.remove('hidden');
    modalContent.innerHTML = '<div class="text-center" style="padding:24px"><div class="spinner"></div></div>';

    try {
      const data = await apiGet(`/api/history/${historyId}/markdown`);
      modalContent.innerHTML = marked.parse(data.content || '');
    } catch (err) {
      modalContent.innerHTML = `<p style="color:var(--color-danger);">마크다운을 불러올 수 없습니다: ${err.message}</p>`;
    }
  }

  modalClose.addEventListener('click', () => modal.classList.add('hidden'));
  modal.addEventListener('click', (e) => {
    if (e.target === modal) modal.classList.add('hidden');
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !modal.classList.contains('hidden')) {
      modal.classList.add('hidden');
    }
  });

  // ------------------------------------------------------------------
  // Events
  // ------------------------------------------------------------------

  filterForm.addEventListener('submit', (e) => {
    e.preventDefault();
    loadHistory(1);
  });

  document.getElementById('search-btn').addEventListener('click', () => {
    loadHistory(1);
  });

  // ------------------------------------------------------------------
  // Init
  // ------------------------------------------------------------------

  loadHistory(1);
})();
