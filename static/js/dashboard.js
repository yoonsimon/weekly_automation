/**
 * dashboard.js - 메인 대시보드 페이지 로직 (plain HTML table)
 */

(function () {
  'use strict';

  const modal = document.getElementById('md-modal');
  const modalClose = document.getElementById('md-modal-close');
  const modalContent = document.getElementById('md-modal-content');
  const modalImagesGrid = document.getElementById('modal-images-grid');
  const modalImagesCount = document.getElementById('modal-images-count');
  const btnModalDownloadZip = document.getElementById('btn-modal-download-zip');

  const weekStatusTitle = document.getElementById('week-status-title');
  const articleStatusEl = document.getElementById('article-status');
  const noticeStatusEl = document.getElementById('notice-status');

  const recentTbody = document.getElementById('recent-tbody');

  // ------------------------------------------------------------------
  // Week label helper (matches backend get_week_label)
  // ------------------------------------------------------------------

  function getCurrentWeekLabel() {
    const today = new Date();
    const day = today.getDay();
    const monday = new Date(today);
    monday.setDate(today.getDate() - (day === 0 ? 6 : day - 1));
    const weekOfMonth = Math.floor((monday.getDate() - 1) / 7) + 1;
    return (monday.getMonth() + 1) + '월 ' + weekOfMonth + '주차';
  }

  function getCurrentWeekRange() {
    const today = new Date();
    const day = today.getDay();
    const monday = new Date(today);
    monday.setDate(today.getDate() - (day === 0 ? 6 : day - 1));
    const sunday = new Date(monday);
    sunday.setDate(monday.getDate() + 6);
    return [monday.toISOString().slice(0, 10), sunday.toISOString().slice(0, 10)];
  }

  // ------------------------------------------------------------------
  // Render table
  // ------------------------------------------------------------------

  function renderTable(items) {
    if (items.length === 0) {
      recentTbody.innerHTML = '<tr><td colspan="5" style="text-align:center;padding:24px;color:var(--color-text-muted);">이력 없음</td></tr>';
      return;
    }
    recentTbody.innerHTML = items.map(function (item) {
      return '<tr>' +
        '<td>' + escapeHtml(formatWeekLabel(item.week_range)) + '</td>' +
        '<td style="text-align:center;">' + item.article_count + '건</td>' +
        '<td style="text-align:center;"><span class="badge ' + statusBadgeClass(item.status) + '">' + escapeHtml(item.status) + '</span></td>' +
        '<td>' + formatDate(item.created_at) + '</td>' +
        '<td style="text-align:center;"><button class="btn btn--outline btn--sm" data-view-id="' + item.id + '">조회</button></td>' +
        '</tr>';
    }).join('');

    recentTbody.querySelectorAll('[data-view-id]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        openMarkdown(btn.dataset.viewId);
      });
    });
  }

  // ------------------------------------------------------------------
  // Load recent history + derive quick status
  // ------------------------------------------------------------------

  async function loadRecent() {
    try {
      const data = await apiGet('/api/history/recent');
      const items = Array.isArray(data) ? data : (data.items || []);

      renderTable(items);
      updateQuickStatus(items);
    } catch (err) {
      recentTbody.innerHTML = '<tr><td colspan="5" style="text-align:center;padding:24px;color:var(--color-text-muted);">이력 없음</td></tr>';
      showToast(err.message, 'error');
    }
  }

  function updateQuickStatus(items) {
    // Set week label
    var weekLabel = getCurrentWeekLabel();
    weekStatusTitle.textContent = '이번 주 현황 (' + weekLabel + ')';

    // Find this week's article entry
    var range = getCurrentWeekRange();
    var curMonday = range[0];
    var thisWeekArticle = items.find(function (item) {
      if (!item.week_range || item.week_range.length < 2) return false;
      return item.week_range[0] === curMonday;
    });

    if (thisWeekArticle) {
      setStatusBadge(articleStatusEl, thisWeekArticle.status);
    } else {
      setStatusBadge(articleStatusEl, '미완료');
    }

    // Notice status — no history tracking, always show pending
    setStatusBadge(noticeStatusEl, '미완료');
  }

  function setStatusBadge(el, status) {
    var classMap = {
      '미완료': 'quick-status__badge--pending',
      '생성중': 'quick-status__badge--pending',
      '미리보기': 'quick-status__badge--preview',
      '업로드완료': 'quick-status__badge--uploaded',
    };
    var labelMap = {
      '미완료': '미완료',
      '생성중': '생성 중',
      '미리보기': '미리보기',
      '업로드완료': '업로드 완료',
    };

    var cls = classMap[status] || classMap['미완료'];
    var label = labelMap[status] || status;

    el.className = 'quick-status__badge ' + cls;
    el.innerHTML = '<span class="quick-status__dot"></span><span>' + label + '</span>';
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
      var data = await apiGet('/api/history/' + historyId + '/markdown');
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

  modalClose.addEventListener('click', function () {
    modal.classList.add('hidden');
  });

  modal.addEventListener('click', function (e) {
    if (e.target === modal) modal.classList.add('hidden');
  });

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && !modal.classList.contains('hidden')) {
      modal.classList.add('hidden');
    }
  });

  // ------------------------------------------------------------------
  // 전체 생성
  // ------------------------------------------------------------------

  var btnGenerateAll = document.getElementById('btn-generate-all');
  if (btnGenerateAll) {
    btnGenerateAll.addEventListener('click', function () {
      // 기사 생성 페이지로 이동 (자동 시작)
      window.location.href = '/generate?autostart=1';
      // 공지사항 수집을 새 탭에서 자동 시작
      window.open('/notices?autostart=1', '_blank');
    });
  }

  // ------------------------------------------------------------------
  // Init
  // ------------------------------------------------------------------

  loadRecent();
})();
