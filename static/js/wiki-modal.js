/**
 * wiki-modal.js - 두레이 위키 URL 모달 공유 모듈
 *
 * Usage:
 *   const modal = createWikiUrlModal({
 *     modalEl, inputEl, errorEl, resolvedEl, resolvedNameEl, resolvedIdEl,
 *     recentSectionEl, recentListEl, verifyBtnEl, cancelBtnEl, uploadBtnEl,
 *     onUpload: function(pageId, wikiId) { ... }
 *   });
 *   modal.show();
 */

// eslint-disable-next-line no-unused-vars
function createWikiUrlModal(opts) {
  'use strict';

  var RECENT_PAGES_KEY = 'dooray_recent_wiki_pages';
  var MAX_RECENT_PAGES = 5;

  var selectedParentPageId = null;
  var selectedWikiId = null;

  function _escapeHtml(str) {
    if (!str) return '';
    var div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  function getRecentPages() {
    try {
      return JSON.parse(localStorage.getItem(RECENT_PAGES_KEY)) || [];
    } catch (e) { return []; }
  }

  function saveRecentPage(page) {
    var pages = getRecentPages();
    pages = pages.filter(function (p) { return p.page_id !== page.page_id; });
    pages.unshift(page);
    if (pages.length > MAX_RECENT_PAGES) pages = pages.slice(0, MAX_RECENT_PAGES);
    localStorage.setItem(RECENT_PAGES_KEY, JSON.stringify(pages));
  }

  function renderRecentPages() {
    var pages = getRecentPages();
    if (pages.length === 0) {
      opts.recentSectionEl.classList.add('hidden');
      return;
    }
    opts.recentSectionEl.classList.remove('hidden');
    opts.recentListEl.innerHTML = pages.map(function (p) {
      return '<label class="recent-page-item" style="display:flex; align-items:center; gap:10px; padding:10px 12px; border:1px solid var(--color-border); border-radius:8px; cursor:pointer; transition:border-color 0.15s;">' +
        '<input type="radio" name="recent-page" value="' + _escapeHtml(p.page_id) + '" style="accent-color:var(--color-primary);">' +
        '<div style="flex:1; min-width:0;">' +
        '<div style="font-weight:600; font-size:0.875rem; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">' + _escapeHtml(p.subject) + '</div>' +
        '<div style="font-size:0.75rem; color:var(--color-text-muted); white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">' + _escapeHtml(p.url) + '</div>' +
        '</div></label>';
    }).join('');

    opts.recentListEl.querySelectorAll('input[name="recent-page"]').forEach(function (radio) {
      radio.addEventListener('change', function () {
        selectedParentPageId = radio.value;
        var recentPage = getRecentPages().find(function (p) { return p.page_id === radio.value; });
        selectedWikiId = recentPage ? recentPage.wiki_id : null;
        opts.inputEl.value = '';
        opts.resolvedEl.classList.add('hidden');
        opts.errorEl.classList.add('hidden');
        opts.uploadBtnEl.disabled = false;
        opts.recentListEl.querySelectorAll('.recent-page-item').forEach(function (el) {
          el.style.borderColor = 'var(--color-border)';
          el.style.background = '';
        });
        radio.closest('.recent-page-item').style.borderColor = 'var(--color-primary)';
        radio.closest('.recent-page-item').style.background = 'var(--color-primary-light)';
      });
    });
  }

  function reset() {
    selectedParentPageId = null;
    selectedWikiId = null;
    opts.inputEl.value = '';
    opts.errorEl.classList.add('hidden');
    opts.resolvedEl.classList.add('hidden');
    opts.uploadBtnEl.disabled = true;
    renderRecentPages();
  }

  function show() {
    reset();
    opts.modalEl.classList.remove('hidden');
    opts.inputEl.focus();
  }

  // "확인" 버튼 — URL 파싱 후 Dooray API 조회
  opts.verifyBtnEl.addEventListener('click', async function () {
    var url = opts.inputEl.value.trim();
    if (!url) {
      opts.errorEl.textContent = 'URL을 입력해주세요.';
      opts.errorEl.classList.remove('hidden');
      opts.resolvedEl.classList.add('hidden');
      return;
    }

    opts.errorEl.classList.add('hidden');
    opts.resolvedEl.classList.add('hidden');
    opts.verifyBtnEl.disabled = true;
    opts.verifyBtnEl.innerHTML = '<span class="spinner" style="width:12px;height:12px;border-width:2px;"></span>';

    try {
      var data = await apiPost('/api/upload/resolve-wiki-url', { url: url });
      opts.resolvedNameEl.textContent = data.subject || '(제목 없음)';
      opts.resolvedIdEl.textContent = 'Page ID: ' + data.page_id;
      opts.resolvedEl.classList.remove('hidden');

      opts.recentListEl.querySelectorAll('input[name="recent-page"]').forEach(function (r) { r.checked = false; });
      opts.recentListEl.querySelectorAll('.recent-page-item').forEach(function (el) {
        el.style.borderColor = 'var(--color-border)';
        el.style.background = '';
      });

      selectedParentPageId = data.page_id;
      selectedWikiId = data.wiki_id || null;
      opts.uploadBtnEl.disabled = false;

      saveRecentPage({ page_id: data.page_id, wiki_id: data.wiki_id, subject: data.subject, url: data.url });
    } catch (err) {
      opts.errorEl.textContent = err.message;
      opts.errorEl.classList.remove('hidden');
      selectedParentPageId = null;
      opts.uploadBtnEl.disabled = true;
    } finally {
      opts.verifyBtnEl.disabled = false;
      opts.verifyBtnEl.textContent = '확인';
    }
  });

  // Enter → 확인
  opts.inputEl.addEventListener('keydown', function (e) {
    if (e.key === 'Enter') opts.verifyBtnEl.click();
  });

  // 모달 "업로드" 버튼
  opts.uploadBtnEl.addEventListener('click', function () {
    if (!selectedParentPageId) return;
    if (opts.onUpload) opts.onUpload(selectedParentPageId, selectedWikiId);
  });

  // 취소
  opts.cancelBtnEl.addEventListener('click', function () {
    opts.modalEl.classList.add('hidden');
  });

  // 오버레이 클릭
  opts.modalEl.addEventListener('click', function (e) {
    if (e.target === opts.modalEl) opts.modalEl.classList.add('hidden');
  });

  return {
    show: show,
    reset: reset,
    getSelectedPageId: function () { return selectedParentPageId; },
    getSelectedWikiId: function () { return selectedWikiId; },
  };
}
