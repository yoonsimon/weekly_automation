/**
 * log.js - 전체 이력 페이지 로직 (plain HTML table + native date inputs)
 */

(function () {
  'use strict';

  const PER_PAGE = 10;

  const filterForm = document.getElementById('filter-form');
  const searchText = document.getElementById('search-text');
  const resetBtn = document.getElementById('reset-btn');
  const dateFrom = document.getElementById('date-from');
  const dateTo = document.getElementById('date-to');

  const modal = document.getElementById('md-modal');
  const modalClose = document.getElementById('md-modal-close');
  const modalContent = document.getElementById('md-modal-content');
  const modalImagesGrid = document.getElementById('modal-images-grid');
  const modalImagesCount = document.getElementById('modal-images-count');
  const btnModalDownloadZip = document.getElementById('btn-modal-download-zip');

  const logTbody = document.getElementById('log-tbody');

  let currentPage = 1;
  let totalPages = 1;

  // ------------------------------------------------------------------
  // Render table
  // ------------------------------------------------------------------

  function renderTable(items) {
    if (items.length === 0) {
      logTbody.innerHTML = '<tr><td colspan="5" style="text-align:center;padding:24px;color:var(--color-text-muted);">이력 없음</td></tr>';
      return;
    }
    logTbody.innerHTML = items.map(function (item) {
      return '<tr>' +
        '<td>' + escapeHtml(formatWeekLabel(item.week_range)) + '</td>' +
        '<td style="text-align:center;">' + item.article_count + '건</td>' +
        '<td style="text-align:center;"><span class="badge ' + statusBadgeClass(item.status) + '">' + escapeHtml(item.status) + '</span></td>' +
        '<td>' + formatDate(item.created_at) + '</td>' +
        '<td style="text-align:center;"><button class="btn btn--outline btn--sm" data-view-id="' + item.id + '">조회</button></td>' +
        '</tr>';
    }).join('');

    logTbody.querySelectorAll('[data-view-id]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        openMarkdown(btn.dataset.viewId);
      });
    });
  }

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

    try {
      const data = await apiGet(`/api/history?${params.toString()}`);
      totalPages = Math.max(1, Math.ceil(data.total / PER_PAGE));

      const items = data.items || [];
      renderTable(items);
      renderPagination();
    } catch (err) {
      logTbody.innerHTML = '<tr><td colspan="5" style="text-align:center;padding:24px;color:var(--color-text-muted);">이력 없음</td></tr>';
      showToast(err.message, 'error');
    }
  }

  // ------------------------------------------------------------------
  // Custom Pagination
  // ------------------------------------------------------------------

  const paginationEl = document.getElementById('custom-pagination');

  function renderPagination() {
    if (totalPages <= 1) {
      paginationEl.innerHTML = '';
      return;
    }

    var html = '';
    html += '<button class="pg-btn" data-page="' + (currentPage - 1) + '"' + (currentPage <= 1 ? ' disabled' : '') + '>&laquo;</button>';

    var startPage = Math.max(1, currentPage - 2);
    var endPage = Math.min(totalPages, startPage + 4);
    if (endPage - startPage < 4) startPage = Math.max(1, endPage - 4);

    for (var i = startPage; i <= endPage; i++) {
      html += '<button class="pg-btn' + (i === currentPage ? ' pg-btn--active' : '') + '" data-page="' + i + '">' + i + '</button>';
    }

    html += '<button class="pg-btn" data-page="' + (currentPage + 1) + '"' + (currentPage >= totalPages ? ' disabled' : '') + '>&raquo;</button>';
    paginationEl.innerHTML = html;

    paginationEl.querySelectorAll('.pg-btn').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var page = parseInt(btn.dataset.page, 10);
        if (page >= 1 && page <= totalPages && page !== currentPage) {
          loadHistory(page);
        }
      });
    });
  }

  // ------------------------------------------------------------------
  // Markdown modal
  // ------------------------------------------------------------------

  let currentHistoryId = null;

  async function openMarkdown(historyId) {
    currentHistoryId = historyId;
    modal.classList.remove('hidden');

    // Reset to markdown tab
    document.querySelectorAll('.modal-tab-btn').forEach(function (b) { b.classList.remove('active'); });
    document.querySelectorAll('.modal-tab-panel').forEach(function (p) { p.classList.remove('active'); });
    document.querySelector('[data-modal-tab="modal-tab-md"]').classList.add('active');
    document.getElementById('modal-tab-md').classList.add('active');

    modalContent.innerHTML = '<div class="text-center" style="padding:24px"><div class="spinner"></div></div>';
    modalImagesGrid.innerHTML = '';
    modalImagesCount.textContent = '';

    try {
      const data = await apiGet('/api/history/' + historyId + '/markdown');
      modalContent.innerHTML = marked.parse(data.content || '');

      // Collect image sources before replacing
      var images = [];
      modalContent.querySelectorAll('img').forEach(function (img) {
        images.push(img.getAttribute('src'));
        var placeholder = document.createElement('span');
        placeholder.className = 'md-preview__image-placeholder';
        placeholder.textContent = '[이미지]';
        img.replaceWith(placeholder);
      });

      // Build images grid
      renderModalImages(images);
    } catch (err) {
      modalContent.innerHTML = '<p style="color:var(--color-danger);">마크다운을 불러올 수 없습니다: ' + escapeHtml(err.message) + '</p>';
    }
  }

  function renderModalImages(imageSrcs) {
    if (imageSrcs.length === 0) {
      modalImagesCount.textContent = '이미지 0건';
      modalImagesGrid.innerHTML = '<div class="empty-state"><div class="empty-state__text">이미지 없음</div></div>';
      btnModalDownloadZip.disabled = true;
      return;
    }

    modalImagesCount.textContent = '이미지 ' + imageSrcs.length + '건';
    btnModalDownloadZip.disabled = false;

    modalImagesGrid.innerHTML = imageSrcs
      .map(function (src) {
        var displaySrc = src.startsWith('http') ? src : '/output/' + src;
        var downloadSrc = '/output/' + src;
        return '<div class="image-card">' +
          '<img class="image-card__preview" src="' + displaySrc + '" alt="" loading="lazy" onerror="this.style.display=\'none\'">' +
          '<div class="image-card__info">' +
          '<div class="image-card__actions">' +
          '<a href="' + downloadSrc + '" download class="btn btn--outline btn--sm">다운로드</a>' +
          '</div></div></div>';
      })
      .join('');
  }

  // Modal tab switching
  document.querySelectorAll('.modal-tab-btn').forEach(function (btn) {
    btn.addEventListener('click', function () {
      document.querySelectorAll('.modal-tab-btn').forEach(function (b) { b.classList.remove('active'); });
      document.querySelectorAll('.modal-tab-panel').forEach(function (p) { p.classList.remove('active'); });
      btn.classList.add('active');
      document.getElementById(btn.dataset.modalTab).classList.add('active');
    });
  });

  // Modal ZIP download
  btnModalDownloadZip.addEventListener('click', async function () {
    if (!currentHistoryId) return;
    btnModalDownloadZip.disabled = true;
    btnModalDownloadZip.innerHTML = '<span class="spinner" style="width:14px;height:14px;border-width:2px;"></span> 다운로드 중...';

    try {
      var res = await fetch('/api/history/' + currentHistoryId + '/images/zip');
      if (!res.ok) {
        var err = await res.json().catch(function () { return { detail: '다운로드 실패' }; });
        throw new Error(err.detail || '다운로드 실패');
      }
      var blob = await res.blob();
      var url = URL.createObjectURL(blob);
      var a = document.createElement('a');
      a.href = url;
      a.download = 'images.zip';
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      showToast('이미지 ZIP 다운로드 완료', 'success');
    } catch (err) {
      showToast('ZIP 다운로드 실패: ' + err.message, 'error');
    } finally {
      btnModalDownloadZip.disabled = false;
      btnModalDownloadZip.textContent = '전체 다운로드 (ZIP)';
    }
  });

  modalClose.addEventListener('click', function () { closeModal(modal); });
  modal.addEventListener('click', function (e) {
    if (e.target === modal) closeModal(modal);
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && !modal.classList.contains('hidden')) {
      closeModal(modal);
    }
  });

  // ------------------------------------------------------------------
  // Filter Events
  // ------------------------------------------------------------------

  filterForm.addEventListener('submit', function (e) {
    e.preventDefault();
    loadHistory(1);
  });

  document.getElementById('search-btn').addEventListener('click', function () {
    loadHistory(1);
  });

  // 초기화 버튼
  resetBtn.addEventListener('click', function () {
    dateFrom.value = '';
    dateTo.value = '';
    searchText.value = '';
    loadHistory(1);
  });

  // ------------------------------------------------------------------
  // Init
  // ------------------------------------------------------------------

  loadHistory(1);
})();
