/**
 * dashboard.js - 메인 대시보드 페이지 로직
 */

(function () {
  'use strict';

  const tbody = document.getElementById('recent-tbody');
  const modal = document.getElementById('md-modal');
  const modalClose = document.getElementById('md-modal-close');
  const modalContent = document.getElementById('md-modal-content');

  // ------------------------------------------------------------------
  // Load recent history
  // ------------------------------------------------------------------

  async function loadRecent() {
    try {
      const data = await apiGet('/api/history/recent');
      renderTable(data.items || []);
    } catch (err) {
      tbody.innerHTML = `<tr><td colspan="5" class="text-center" style="padding:32px;color:var(--color-text-secondary);">이력을 불러올 수 없습니다.</td></tr>`;
      showToast(err.message, 'error');
    }
  }

  function renderTable(items) {
    if (items.length === 0) {
      tbody.innerHTML = `<tr><td colspan="5" class="text-center" style="padding:32px;color:var(--color-text-secondary);">생성 이력이 없습니다.</td></tr>`;
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

    // Bind view buttons
    tbody.querySelectorAll('[data-view-id]').forEach((btn) => {
      btn.addEventListener('click', () => openMarkdown(btn.dataset.viewId));
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

  modalClose.addEventListener('click', () => {
    modal.classList.add('hidden');
  });

  modal.addEventListener('click', (e) => {
    if (e.target === modal) modal.classList.add('hidden');
  });

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !modal.classList.contains('hidden')) {
      modal.classList.add('hidden');
    }
  });

  // ------------------------------------------------------------------
  // Init
  // ------------------------------------------------------------------

  loadRecent();
})();
