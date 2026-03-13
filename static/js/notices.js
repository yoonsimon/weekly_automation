/**
 * notices.js - 공지사항 수집 + 선별 페이지 로직 (TOAST UI Grid)
 *
 * States:
 *   1. idle     - "수집하기" 버튼
 *   2. loading  - 스피너
 *   3. results  - 체크박스로 선별 / MD 미리보기
 */

(function () {
  'use strict';

  // ------------------------------------------------------------------
  // Source → (경쟁사명, 구분명) mapping (matches backend)
  // ------------------------------------------------------------------

  var SOURCE_MAP = {
    '아임웹':           ['아임웹',  '공지사항'],
    '카페24-쇼핑몰':    ['카페24',  '공지사항'],
    '카페24-기능':      ['카페24',  '기능'],
    '카페24-업데이트':   ['카페24',  '업데이트'],
    '카페24-개발자센터': ['카페24',  '개발자센터'],
    '메이크샵':         ['메이크샵', '공지사항'],
  };

  // ------------------------------------------------------------------
  // DOM refs
  // ------------------------------------------------------------------

  var stateIdle = document.getElementById('state-idle');
  var stateLoading = document.getElementById('state-loading');
  var stateResults = document.getElementById('state-results');

  var btnCollect = document.getElementById('btn-collect');
  var btnRecollect = document.getElementById('btn-recollect');
  var btnUpload = document.getElementById('btn-upload');

  var weekRangeLabel = document.getElementById('week-range-label');
  var totalCountLabel = document.getElementById('total-count-label');
  var platformsContainer = document.getElementById('platforms-container');
  var mdPreviewContent = document.getElementById('md-preview-content');

  // Wiki URL modal refs
  var wikiUrlModal = document.getElementById('wiki-url-modal');
  var wikiUrlInput = document.getElementById('wiki-url-input');
  var wikiUrlError = document.getElementById('wiki-url-error');
  var wikiUrlResolved = document.getElementById('wiki-url-resolved');
  var resolvedPageName = document.getElementById('resolved-page-name');
  var resolvedPageId = document.getElementById('resolved-page-id');
  var recentPagesSection = document.getElementById('recent-pages-section');
  var recentPagesList = document.getElementById('recent-pages-list');
  var btnWikiUrlVerify = document.getElementById('btn-wiki-url-verify');
  var btnWikiUrlCancel = document.getElementById('btn-wiki-url-cancel');
  var btnWikiUrlUpload = document.getElementById('btn-wiki-url-upload');

  // Tab buttons
  document.querySelectorAll('.tab-btn').forEach(function (btn) {
    btn.addEventListener('click', function () {
      document.querySelectorAll('.tab-btn').forEach(function (b) { b.classList.remove('active'); });
      document.querySelectorAll('.tab-panel').forEach(function (p) { p.classList.remove('active'); });
      btn.classList.add('active');
      document.getElementById(btn.dataset.tab).classList.add('active');
    });
  });

  // ------------------------------------------------------------------
  // State
  // ------------------------------------------------------------------

  var collectResult = null;
  var selectedParentPageId = null;
  var selectedWikiId = null;
  var gridInstances = [];       // { grid, platformName }[]
  var allNoticesFlat = [];      // { source, date, title, url }[]

  function showState(name) {
    stateIdle.classList.toggle('hidden', name !== 'idle');
    stateLoading.classList.toggle('hidden', name !== 'loading');
    stateResults.classList.toggle('hidden', name !== 'results');
  }

  // ------------------------------------------------------------------
  // Markdown generation from selected notices
  // ------------------------------------------------------------------

  function getSelectedNotices() {
    var selected = [];
    gridInstances.forEach(function (entry) {
      var checkedRows = entry.grid.getCheckedRows();
      checkedRows.forEach(function (row) {
        selected.push({
          source: entry.platformName,
          date: row.rawDate || null,
          title: row.title,
          url: row.url,
        });
      });
    });
    // Sort by date ascending (null last)
    selected.sort(function (a, b) {
      if (!a.date && !b.date) return 0;
      if (!a.date) return 1;
      if (!b.date) return -1;
      return a.date < b.date ? -1 : a.date > b.date ? 1 : 0;
    });
    return selected;
  }

  function buildMarkdownTable(notices) {
    var lines = [
      '| | 경쟁사명 | 구분명 | 내용 | 주간리포트 |',
      '|---|---|---|---|---|',
    ];
    notices.forEach(function (n) {
      var dateStr = '';
      if (n.date) {
        var d = new Date(n.date + 'T00:00:00');
        dateStr = (d.getMonth() + 1) + '/' + d.getDate();
      }
      var mapped = SOURCE_MAP[n.source] || [n.source, '공지사항'];
      var content = '[' + n.title + '](' + n.url + ')';
      lines.push('| ' + dateStr + ' | ' + mapped[0] + ' | ' + mapped[1] + ' | ' + content + ' | |');
    });
    return lines.join('\n');
  }

  function updateSelectionState() {
    var selected = getSelectedNotices();
    var total = allNoticesFlat.length;
    totalCountLabel.textContent = '선택 ' + selected.length + ' / 전체 ' + total + '건';

    // Update markdown preview
    var md = buildMarkdownTable(selected);
    collectResult._selectedMarkdown = md;
    if (typeof marked !== 'undefined') {
      mdPreviewContent.innerHTML = marked.parse(md);
    }

    // Update per-platform counts
    gridInstances.forEach(function (entry) {
      var badge = document.getElementById('selected-count-' + entry.platformName);
      if (badge) {
        var checked = entry.grid.getCheckedRows().length;
        var gridTotal = entry.grid.getData().length;
        badge.textContent = checked + '/' + gridTotal;
      }
    });
  }

  // ------------------------------------------------------------------
  // Collect
  // ------------------------------------------------------------------

  async function doCollect() {
    showState('loading');
    try {
      var data = await apiPost('/api/notices/collect');
      collectResult = data;
      renderResults(data);
      showState('results');
    } catch (err) {
      showToast('수집 실패: ' + err.message, 'error');
      showState('idle');
    }
  }

  btnCollect.addEventListener('click', doCollect);
  btnRecollect.addEventListener('click', doCollect);

  // ------------------------------------------------------------------
  // Render results (TOAST UI Grid per platform with checkboxes)
  // ------------------------------------------------------------------

  function renderResults(data) {
    // Destroy previous grid instances
    gridInstances.forEach(function (entry) {
      try { entry.grid.destroy(); } catch (e) { /* ignore */ }
    });
    gridInstances.length = 0;
    allNoticesFlat.length = 0;

    // Week range label
    if (data.week_range && data.week_range.length >= 2) {
      weekRangeLabel.textContent = formatWeekLabel(data.week_range);
    }

    // Platforms
    platformsContainer.innerHTML = '';
    for (var i = 0; i < data.platforms.length; i++) {
      var platform = data.platforms[i];
      var card = document.createElement('div');
      card.className = 'category-group';

      var header = document.createElement('div');
      header.className = 'category-group__header';
      header.style.cssText = 'display:flex; align-items:center; justify-content:space-between;';
      header.innerHTML =
        '<div style="display:flex; align-items:center; gap:8px;">' +
          '<span class="category-group__name">' + escapeHtml(platform.name) + '</span>' +
          '<span id="selected-count-' + escapeHtml(platform.name) + '" class="badge" style="font-size:11px;">' + platform.count + '/' + platform.count + '</span>' +
        '</div>' +
        '<button class="btn btn--outline btn--xs toggle-all-btn" data-platform="' + escapeHtml(platform.name) + '">전체 해제</button>';
      card.appendChild(header);

      if (platform.notices.length === 0) {
        var empty = document.createElement('p');
        if (platform.status === 'error') {
          empty.style.cssText = 'padding:16px; color:var(--color-danger, #e53e3e); font-size:0.8125rem; text-align:center;';
          empty.textContent = '수집 실패' + (platform.error ? ': ' + platform.error : '');
        } else {
          empty.style.cssText = 'padding:16px; color:var(--color-text-muted); font-size:0.8125rem; text-align:center;';
          empty.textContent = '새 공지 없음';
        }
        card.appendChild(empty);
        platformsContainer.appendChild(card);
      } else {
        var gridContainer = document.createElement('div');
        card.appendChild(gridContainer);
        platformsContainer.appendChild(card);

        var gridData = platform.notices.map(function (n) {
          allNoticesFlat.push({
            source: platform.name,
            date: n.date || null,
            title: n.title,
            url: n.url,
          });
          return {
            rawDate: n.date || null,
            date: n.date || '-',
            title: n.title,
            url: n.url || '#',
          };
        });

        var gridInstance = new tui.Grid({
          el: gridContainer,
          columns: [
            { header: '날짜', name: 'date', width: 100 },
            {
              header: '제목',
              name: 'title',
              renderer: { type: NoticeTitleRenderer },
            },
          ],
          rowHeaders: ['checkbox'],
          data: gridData,
          bodyHeight: 'auto',
          scrollX: false,
          scrollY: false,
          rowHeight: 40,
          minBodyHeight: 40,
        });

        // Check all rows by default
        gridInstance.checkAll();

        // Listen for check/uncheck events
        gridInstance.on('check', function () { updateSelectionState(); });
        gridInstance.on('uncheck', function () { updateSelectionState(); });
        gridInstance.on('checkAll', function () { updateSelectionState(); });
        gridInstance.on('uncheckAll', function () { updateSelectionState(); });

        gridInstances.push({ grid: gridInstance, platformName: platform.name });
      }
    }

    // Toggle all buttons
    document.querySelectorAll('.toggle-all-btn').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var platformName = btn.dataset.platform;
        var entry = gridInstances.find(function (e) { return e.platformName === platformName; });
        if (!entry) return;
        var checkedCount = entry.grid.getCheckedRows().length;
        var totalCount = entry.grid.getData().length;
        if (checkedCount === totalCount) {
          entry.grid.uncheckAll();
          btn.textContent = '전체 선택';
        } else {
          entry.grid.checkAll();
          btn.textContent = '전체 해제';
        }
      });
    });

    // Initial state
    updateSelectionState();
  }

  // ------------------------------------------------------------------
  // Wiki URL Modal (shared module)
  // ------------------------------------------------------------------

  var wikiModal = createWikiUrlModal({
    modalEl: wikiUrlModal,
    inputEl: wikiUrlInput,
    errorEl: wikiUrlError,
    resolvedEl: wikiUrlResolved,
    resolvedNameEl: resolvedPageName,
    resolvedIdEl: resolvedPageId,
    recentSectionEl: recentPagesSection,
    recentListEl: recentPagesList,
    verifyBtnEl: btnWikiUrlVerify,
    cancelBtnEl: btnWikiUrlCancel,
    uploadBtnEl: btnWikiUrlUpload,
    onUpload: function (parentPageId, wikiId) {
      doUpload(parentPageId, wikiId);
    },
  });

  // ------------------------------------------------------------------
  // Upload
  // ------------------------------------------------------------------

  async function doUpload(parentPageId, wikiId) {
    var selected = getSelectedNotices();
    if (selected.length === 0) {
      showToast('업로드할 공지사항을 선택해주세요.', 'error');
      return;
    }

    wikiUrlModal.classList.add('hidden');
    btnUpload.disabled = true;
    btnUpload.innerHTML = '<span class="spinner" style="width:14px;height:14px;border-width:2px;"></span> 업로드 중...';

    try {
      var markdownRaw = buildMarkdownTable(selected);
      var body = {
        parent_page_id: parentPageId,
        wiki_id: wikiId,
        markdown_raw: markdownRaw,
      };
      var data = await apiPost('/api/notices/upload', body);
      showToast('업로드 완료!', 'success');
      if (data.dooray_page_url) {
        btnUpload.innerHTML = '<a href="' + escapeHtml(data.dooray_page_url) + '" target="_blank" rel="noopener" style="color:#fff; text-decoration:none;">업로드 완료 - 페이지 바로가기 &rarr;</a>';
      } else {
        btnUpload.textContent = '업로드 완료';
      }
    } catch (err) {
      showToast('업로드 실패: ' + err.message, 'error');
      btnUpload.disabled = false;
      btnUpload.textContent = '두레이 업로드';
    }
  }

  btnUpload.addEventListener('click', function () {
    if (!collectResult) {
      showToast('먼저 공지사항을 수집해주세요.', 'error');
      return;
    }
    var selected = getSelectedNotices();
    if (selected.length === 0) {
      showToast('업로드할 공지사항을 선택해주세요.', 'error');
      return;
    }
    wikiModal.show();
  });

  // ------------------------------------------------------------------
  // Week label helper
  // ------------------------------------------------------------------

  function getCurrentWeekLabel() {
    var today = new Date();
    var day = today.getDay();
    var monday = new Date(today);
    monday.setDate(today.getDate() - (day === 0 ? 6 : day - 1));
    var weekOfMonth = Math.floor((monday.getDate() - 1) / 7) + 1;
    return (monday.getMonth() + 1) + '월 ' + weekOfMonth + '주차';
  }

  // Set idle title
  var noticeWeekTitle = document.getElementById('notice-week-title');
  if (noticeWeekTitle) {
    noticeWeekTitle.textContent = getCurrentWeekLabel() + ' 플랫폼 공지사항';
  }

})();