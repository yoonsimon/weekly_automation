/**
 * generate.js - 기사 생성 페이지 로직
 *
 * States:
 *   1. progress  - SSE로 진행률 표시
 *   2. articles  - 기사 카드 목록 / MD 미리보기
 *   3. complete  - 업로드 완료
 */

(function () {
  'use strict';

  // ------------------------------------------------------------------
  // DOM refs
  // ------------------------------------------------------------------

  const stateProgress = document.getElementById('state-progress');
  const stateArticles = document.getElementById('state-articles');
  const stateComplete = document.getElementById('state-complete');

  const progressBar = document.getElementById('progress-bar');
  const progressStep = document.getElementById('progress-step');
  const progressMessage = document.getElementById('progress-message');

  const cardsMain = document.getElementById('cards-main');
  const cardsMarket = document.getElementById('cards-market');
  const cardsOther = document.getElementById('cards-other');

  const mdPreviewContent = document.getElementById('md-preview-content');
  const selectedCountEl = document.getElementById('selected-count');

  const btnReplace = document.getElementById('btn-replace');
  const btnConfirm = document.getElementById('btn-confirm');
  const btnUpload = document.getElementById('btn-upload');

  const replaceModal = document.getElementById('replace-modal');
  const replaceModalBody = document.getElementById('replace-modal-body');
  const btnReplaceCancel = document.getElementById('btn-replace-cancel');
  const btnReplaceRetry = document.getElementById('btn-replace-retry');
  const btnReplaceApprove = document.getElementById('btn-replace-approve');

  const completeMessage = document.getElementById('complete-message');

  // Tab buttons
  document.querySelectorAll('.tab-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.tab-btn').forEach((b) => b.classList.remove('active'));
      document.querySelectorAll('.tab-panel').forEach((p) => p.classList.remove('active'));
      btn.classList.add('active');
      document.getElementById(btn.dataset.tab).classList.add('active');

      // Lazy load preview
      if (btn.dataset.tab === 'tab-preview') loadPreview();
    });
  });

  // ------------------------------------------------------------------
  // State management
  // ------------------------------------------------------------------

  let sessionId = null;
  let articles = { main: [], market: [], other: [] };
  let selectedIndices = new Set();
  let historyId = null;          // set after confirm
  let pendingReplacements = [];  // replacement details during modal

  function showState(name) {
    stateProgress.classList.toggle('hidden', name !== 'progress');
    stateArticles.classList.toggle('hidden', name !== 'articles');
    stateComplete.classList.toggle('hidden', name !== 'complete');
  }

  // ------------------------------------------------------------------
  // Step 1: Start generation & SSE progress
  // ------------------------------------------------------------------

  async function startGeneration() {
    showState('progress');
    try {
      const res = await apiPost('/api/generate/start');
      sessionId = res.session_id;
      connectProgress();
    } catch (err) {
      progressStep.textContent = '오류 발생';
      progressMessage.textContent = err.message;
      showToast(err.message, 'error');
    }
  }

  function connectProgress() {
    const es = connectSSE(
      `/api/generate/${sessionId}/status/stream`,
      onProgress,
      onProgressComplete,
      onProgressError
    );

    // Store ref to close if needed
    window.__generateSSE = es;
  }

  function onProgress(data) {
    const pct = data.total > 0 ? Math.round((data.current / data.total) * 100) : 0;
    progressBar.style.width = pct + '%';
    progressStep.textContent = `${data.step} (${data.current}/${data.total})`;
    progressMessage.textContent = data.message || '';
  }

  function onProgressComplete(data) {
    progressBar.style.width = '100%';
    progressStep.textContent = '완료';
    progressMessage.textContent = '기사 목록을 불러오는 중...';
    loadArticles();
  }

  function onProgressError(err) {
    progressStep.textContent = '오류 발생';
    progressMessage.textContent = err.message;
    showToast(err.message, 'error');
  }

  // ------------------------------------------------------------------
  // Step 2: Load & render articles
  // ------------------------------------------------------------------

  async function loadArticles() {
    try {
      const data = await apiGet(`/api/generate/${sessionId}/articles`);
      articles = data.articles || { main: [], market: [], other: [] };
      renderAllCards();
      showState('articles');
    } catch (err) {
      showToast('기사를 불러올 수 없습니다: ' + err.message, 'error');
    }
  }

  function renderAllCards() {
    selectedIndices.clear();
    updateSelectedCount();

    cardsMain.innerHTML = renderCategoryCards(articles.main || []);
    cardsMarket.innerHTML = renderCategoryCards(articles.market || []);
    cardsOther.innerHTML = renderCategoryCards(articles.other || []);

    bindCardEvents();
  }

  function renderCategoryCards(list) {
    if (list.length === 0) {
      return '<div class="empty-state"><div class="empty-state__text">기사 없음</div></div>';
    }
    return list.map((a) => renderArticleCard(a)).join('');
  }

  function renderArticleCard(a) {
    const scoreClass = scoreBadgeClass(a.score);
    const canReplace = a.replacement_count < a.max_replacements;
    const imgTag = a.image_local
      ? `<img class="article-card__image" src="/output/${a.image_local}" alt="" loading="lazy">`
      : (a.image_url ? `<img class="article-card__image" src="${escapeHtml(a.image_url)}" alt="" loading="lazy">` : '');

    return `
      <div class="article-card" data-index="${a.index}">
        <div class="article-card__header">
          <input type="checkbox" class="article-card__checkbox" data-index="${a.index}" ${!canReplace ? 'disabled title="교체 횟수 초과"' : ''}>
          <div class="article-card__info">
            <div class="article-card__title">
              <a href="${escapeHtml(a.link)}" target="_blank" rel="noopener">${escapeHtml(a.title)}</a>
            </div>
            <div class="article-card__meta">
              <span class="score-badge ${scoreClass}">${a.score}점</span>
              <span>${escapeHtml(a.source)}</span>
              <span>${escapeHtml(a.keyword)}</span>
              <span>${escapeHtml(a.date)}</span>
              ${a.replacement_count > 0 ? `<span style="color:var(--color-text-muted);">교체 ${a.replacement_count}/${a.max_replacements}</span>` : ''}
            </div>
          </div>
        </div>
        <div class="article-card__toggle">
          <button class="article-card__toggle-btn" data-toggle="${a.index}">본문 펼치기 &#9662;</button>
        </div>
        <div class="article-card__body" id="body-${a.index}">
          <div class="article-card__body-inner">
            ${imgTag}
            ${escapeHtml(a.body_full || a.body_preview || '')}
          </div>
        </div>
      </div>
    `;
  }

  function bindCardEvents() {
    // Checkboxes
    document.querySelectorAll('.article-card__checkbox').forEach((cb) => {
      cb.addEventListener('change', () => {
        const idx = parseInt(cb.dataset.index, 10);
        if (cb.checked) {
          selectedIndices.add(idx);
          cb.closest('.article-card').classList.add('selected');
        } else {
          selectedIndices.delete(idx);
          cb.closest('.article-card').classList.remove('selected');
        }
        updateSelectedCount();
      });
    });

    // Accordion toggles
    document.querySelectorAll('.article-card__toggle-btn').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const idx = btn.dataset.toggle;
        const bodyEl = document.getElementById('body-' + idx);
        const isOpen = bodyEl.classList.contains('open');
        bodyEl.classList.toggle('open');
        btn.innerHTML = isOpen ? '본문 펼치기 &#9662;' : '본문 접기 &#9652;';
      });
    });
  }

  function updateSelectedCount() {
    selectedCountEl.textContent = `선택: ${selectedIndices.size}건`;
    btnReplace.disabled = selectedIndices.size === 0;
  }

  // ------------------------------------------------------------------
  // MD Preview (Tab 2)
  // ------------------------------------------------------------------

  let previewLoaded = false;

  async function loadPreview() {
    if (previewLoaded) return;
    mdPreviewContent.innerHTML = '<div class="text-center" style="padding:32px"><div class="spinner"></div></div>';
    try {
      const data = await apiGet(`/api/generate/${sessionId}/preview`);
      if (data.markdown_html) {
        mdPreviewContent.innerHTML = data.markdown_html;
      } else if (data.markdown_raw) {
        mdPreviewContent.innerHTML = marked.parse(data.markdown_raw);
      }
      previewLoaded = true;
    } catch (err) {
      mdPreviewContent.innerHTML = `<p style="color:var(--color-danger);">미리보기를 불러올 수 없습니다: ${escapeHtml(err.message)}</p>`;
    }
  }

  // ------------------------------------------------------------------
  // Replacement flow
  // ------------------------------------------------------------------

  btnReplace.addEventListener('click', async () => {
    if (selectedIndices.size === 0) return;

    const indices = Array.from(selectedIndices);
    btnReplace.disabled = true;
    btnReplace.innerHTML = '<span class="spinner" style="width:14px;height:14px;border-width:2px;"></span> 교체 중...';

    try {
      const data = await apiPost(`/api/generate/${sessionId}/replace`, {
        article_indices: indices,
      });
      pendingReplacements = data.replacements || [];
      showReplacementModal(indices);
    } catch (err) {
      showToast('기사 교체 실패: ' + err.message, 'error');
    } finally {
      btnReplace.disabled = selectedIndices.size === 0;
      btnReplace.textContent = '선택 기사 교체';
    }
  });

  function showReplacementModal(indices) {
    replaceModalBody.innerHTML = pendingReplacements.map((r) => {
      const canRetry = r.replacement_count < (r.after.max_replacements || 3);
      return `
        <div class="replacement-item">
          <div class="replacement-item__header">
            <span>기사 #${r.index}</span>
            <span class="replacement-item__count">교체 횟수: ${r.replacement_count}/${r.after.max_replacements || 3}</span>
          </div>
          <div class="replacement-compare">
            <div class="replacement-side replacement-side--before">
              <div class="replacement-side__label">이전</div>
              <div class="replacement-side__title">${escapeHtml(r.before.title)}</div>
              <div class="replacement-side__meta">${escapeHtml(r.before.source)} | ${escapeHtml(r.before.keyword)} | ${r.before.score}점</div>
              <div class="replacement-side__preview">${escapeHtml(r.before.body_preview || '')}</div>
            </div>
            <div class="replacement-side replacement-side--after">
              <div class="replacement-side__label">변경</div>
              <div class="replacement-side__title">${escapeHtml(r.after.title)}</div>
              <div class="replacement-side__meta">${escapeHtml(r.after.source)} | ${escapeHtml(r.after.keyword)} | ${r.after.score}점</div>
              <div class="replacement-side__preview">${escapeHtml(r.after.body_preview || '')}</div>
            </div>
          </div>
        </div>
      `;
    }).join('');

    // Check if any can retry
    const anyCanRetry = pendingReplacements.some(
      (r) => r.replacement_count < (r.after.max_replacements || 3)
    );
    btnReplaceRetry.disabled = !anyCanRetry;

    replaceModal.classList.remove('hidden');
  }

  // Approve replacement
  btnReplaceApprove.addEventListener('click', async () => {
    const indices = pendingReplacements.map((r) => r.index);
    btnReplaceApprove.disabled = true;
    try {
      await apiPost(`/api/generate/${sessionId}/replace/approve`, {
        article_indices: indices,
        action: 'approve',
      });
      replaceModal.classList.add('hidden');
      showToast('교체가 승인되었습니다.', 'success');
      previewLoaded = false;
      await loadArticles();
    } catch (err) {
      showToast('승인 실패: ' + err.message, 'error');
    } finally {
      btnReplaceApprove.disabled = false;
    }
  });

  // Retry replacement
  btnReplaceRetry.addEventListener('click', async () => {
    const indices = pendingReplacements.map((r) => r.index);
    btnReplaceRetry.disabled = true;
    try {
      const data = await apiPost(`/api/generate/${sessionId}/replace/approve`, {
        article_indices: indices,
        action: 'retry',
      });
      pendingReplacements = data.replacements || [];
      showReplacementModal(indices);
      showToast('다시 교체되었습니다.', 'info');
    } catch (err) {
      showToast('재교체 실패: ' + err.message, 'error');
    } finally {
      btnReplaceRetry.disabled = false;
    }
  });

  // Cancel replacement
  btnReplaceCancel.addEventListener('click', async () => {
    const indices = pendingReplacements.map((r) => r.index);
    try {
      await apiPost(`/api/generate/${sessionId}/replace/approve`, {
        article_indices: indices,
        action: 'cancel',
      });
    } catch {
      // Ignore cancel errors
    }
    replaceModal.classList.add('hidden');
    pendingReplacements = [];
  });

  // Close modal on overlay click
  replaceModal.addEventListener('click', (e) => {
    if (e.target === replaceModal) {
      btnReplaceCancel.click();
    }
  });

  // ------------------------------------------------------------------
  // Confirm save
  // ------------------------------------------------------------------

  btnConfirm.addEventListener('click', async () => {
    if (!confirm('현재 기사 목록으로 확정 저장하시겠습니까?')) return;

    btnConfirm.disabled = true;
    btnConfirm.innerHTML = '<span class="spinner" style="width:14px;height:14px;border-width:2px;"></span> 저장 중...';

    try {
      const data = await apiPost(`/api/generate/${sessionId}/confirm`);
      historyId = data.history_id;
      showToast(`확정 저장 완료 (${data.md_filename})`, 'success');
      btnConfirm.textContent = '저장 완료';
      btnUpload.disabled = false;
    } catch (err) {
      showToast('저장 실패: ' + err.message, 'error');
      btnConfirm.disabled = false;
      btnConfirm.textContent = '확정 저장';
    }
  });

  // ------------------------------------------------------------------
  // Upload
  // ------------------------------------------------------------------

  btnUpload.addEventListener('click', async () => {
    if (!historyId) {
      showToast('먼저 확정 저장을 해주세요.', 'error');
      return;
    }

    btnUpload.disabled = true;
    btnUpload.innerHTML = '<span class="spinner" style="width:14px;height:14px;border-width:2px;"></span> 업로드 중...';

    try {
      const data = await apiPost(`/api/upload/${historyId}`);
      showToast('업로드 완료!', 'success');
      completeMessage.textContent = `Dooray 페이지에 업로드되었습니다. (Page ID: ${data.dooray_page_id || '-'})`;
      showState('complete');
    } catch (err) {
      showToast('업로드 실패: ' + err.message, 'error');
      btnUpload.disabled = false;
      btnUpload.textContent = '업로드';
    }
  });

  // ------------------------------------------------------------------
  // Utility
  // ------------------------------------------------------------------

  function escapeHtml(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  // ------------------------------------------------------------------
  // Init
  // ------------------------------------------------------------------

  startGeneration();
})();
