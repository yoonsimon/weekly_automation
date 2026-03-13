/**
 * generate.js - 기사 생성 페이지 로직
 *
 * States:
 *   1. progress  - SSE로 진행률 표시 (스테퍼 + 프로그레스 바)
 *   2. articles  - 기사 카드 목록 / MD 미리보기
 *   3. complete  - 업로드 완료
 */

(function () {
  'use strict';

  // ------------------------------------------------------------------
  // Constants
  // ------------------------------------------------------------------

  const STEP_ORDER = ['collecting', 'scoring', 'scraping', 'ready'];

  // ------------------------------------------------------------------
  // DOM refs
  // ------------------------------------------------------------------

  const stateIdle = document.getElementById('state-idle');
  const stateProgress = document.getElementById('state-progress');
  const stateArticles = document.getElementById('state-articles');
  const stateComplete = document.getElementById('state-complete');
  const btnStart = document.getElementById('btn-start');

  const progressBar = document.getElementById('progress-bar');
  const progressStep = document.getElementById('progress-step');
  const progressMessage = document.getElementById('progress-message');
  const progressError = document.getElementById('progress-error');
  const progressErrorMsg = document.getElementById('progress-error-msg');
  const btnRetry = document.getElementById('btn-retry');

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

  // Images tab
  const imagesGrid = document.getElementById('images-grid');
  const imagesCount = document.getElementById('images-count');
  const btnDownloadZip = document.getElementById('btn-download-zip');

  // Tab buttons
  document.querySelectorAll('.tab-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.tab-btn').forEach((b) => b.classList.remove('active'));
      document.querySelectorAll('.tab-panel').forEach((p) => p.classList.remove('active'));
      btn.classList.add('active');
      document.getElementById(btn.dataset.tab).classList.add('active');

      // Lazy load preview / images
      if (btn.dataset.tab === 'tab-preview') loadPreview();
      if (btn.dataset.tab === 'tab-images') loadImages();
    });
  });

  // ------------------------------------------------------------------
  // State management
  // ------------------------------------------------------------------

  let sessionId = null;
  let articles = { main: [], other: [], market: [] };
  let originalArticles = { main: [], other: [], market: [] }; // preserve original order
  let selectedIndices = new Set();
  let historyId = null;          // set after confirm
  let pendingReplacements = [];  // replacement details during modal

  function showState(name) {
    stateIdle.classList.toggle('hidden', name !== 'idle');
    stateProgress.classList.toggle('hidden', name !== 'progress');
    stateArticles.classList.toggle('hidden', name !== 'articles');
    stateComplete.classList.toggle('hidden', name !== 'complete');
  }

  // ------------------------------------------------------------------
  // Stepper
  // ------------------------------------------------------------------

  function updateStepper(currentStep) {
    const stepEls = document.querySelectorAll('.stepper__step');
    const connEls = document.querySelectorAll('.stepper__connector');
    const currentIdx = STEP_ORDER.indexOf(currentStep);

    stepEls.forEach((el, i) => {
      el.classList.remove('active', 'completed', 'error');
      if (i < currentIdx) {
        el.classList.add('completed');
      } else if (i === currentIdx) {
        el.classList.add('active');
      }
    });

    connEls.forEach((el, i) => {
      el.classList.remove('completed');
      if (i < currentIdx) {
        el.classList.add('completed');
      }
    });
  }

  function setStepperError(failedStep) {
    const stepEls = document.querySelectorAll('.stepper__step');
    const failIdx = STEP_ORDER.indexOf(failedStep);

    stepEls.forEach((el, i) => {
      el.classList.remove('active', 'completed', 'error');
      if (i < failIdx) {
        el.classList.add('completed');
      } else if (i === failIdx) {
        el.classList.add('error');
      }
    });
  }

  function resetStepper() {
    document.querySelectorAll('.stepper__step').forEach((el) => {
      el.classList.remove('active', 'completed', 'error');
    });
    document.querySelectorAll('.stepper__connector').forEach((el) => {
      el.classList.remove('completed');
    });
  }

  // ------------------------------------------------------------------
  // Step 1: Start generation & SSE progress
  // ------------------------------------------------------------------

  let lastStep = 'collecting';

  async function startGeneration() {
    showState('progress');
    resetStepper();
    progressError.classList.add('hidden');
    progressBar.style.width = '0%';
    progressStep.textContent = '준비 중...';
    progressMessage.textContent = '';

    try {
      const res = await apiPost('/api/generate/start');
      sessionId = res.session_id;
      connectProgress();
    } catch (err) {
      // 409: 이전 활성 세션이 있으면 취소 후 재시도
      if (err.message.includes('409')) {
        try {
          await apiPost('/api/generate/cancel');
          const res = await apiPost('/api/generate/start');
          sessionId = res.session_id;
          connectProgress();
          return;
        } catch (retryErr) {
          progressStep.textContent = '오류 발생';
          progressMessage.textContent = retryErr.message;
          showErrorRetry(retryErr.message);
          showToast(retryErr.message, 'error');
          return;
        }
      }
      progressStep.textContent = '오류 발생';
      progressMessage.textContent = err.message;
      showErrorRetry(err.message);
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
    lastStep = data.step || lastStep;
    updateStepper(lastStep);
  }

  function onProgressComplete(data) {
    progressBar.style.width = '100%';
    progressStep.textContent = '완료';
    progressMessage.textContent = '기사 목록을 불러오는 중...';
    updateStepper('ready');
    loadArticles();
  }

  function onProgressError(err) {
    progressStep.textContent = '오류 발생';
    progressMessage.textContent = err.message;
    setStepperError(lastStep);
    showErrorRetry(err.message);
    showToast(err.message, 'error');
  }

  function showErrorRetry(message) {
    progressErrorMsg.textContent = message;
    progressError.classList.remove('hidden');
  }

  btnRetry.addEventListener('click', () => {
    startGeneration();
  });

  // ------------------------------------------------------------------
  // Step 2: Load & render articles
  // ------------------------------------------------------------------

  function renderSkeletons(count) {
    let html = '';
    for (let i = 0; i < count; i++) {
      html += `
        <div class="skeleton-card">
          <div class="skeleton-line skeleton-line--title"></div>
          <div class="skeleton-line skeleton-line--meta"></div>
        </div>
      `;
    }
    return html;
  }

  async function loadArticles() {
    // Show skeleton loading
    showState('articles');
    cardsMain.innerHTML = renderSkeletons(1);
    cardsMarket.innerHTML = renderSkeletons(3);
    cardsOther.innerHTML = renderSkeletons(3);

    try {
      const data = await apiGet(`/api/generate/${sessionId}/articles`);
      articles = data.articles || { main: [], other: [], market: [] };
      // Deep copy for original order preservation
      originalArticles = {
        main: [...(articles.main || [])],
        market: [...(articles.market || [])],
        other: [...(articles.other || [])],
      };
      renderAllCards();
      resetSortSelects();
    } catch (err) {
      showToast('기사를 불러올 수 없습니다: ' + err.message, 'error');
    }
  }

  function renderAllCards() {
    selectedIndices.clear();
    updateSelectedCount();

    cardsMain.innerHTML = renderCategoryCards(articles.main || []);
    cardsOther.innerHTML = renderCategoryCards(articles.other || []);
    cardsMarket.innerHTML = renderCategoryCards(articles.market || []);

    bindCardEvents();
    updateCategoryStats();
    resetSelectAllButtons();
  }

  function renderCategoryCards(list) {
    if (list.length === 0) {
      return '<div class="empty-state"><div class="empty-state__text">기사 없음</div></div>';
    }
    return list.map((a) => renderArticleCard(a)).join('');
  }

  function scrapeStatusBadge(article) {
    if (article.scrape_status === 'partial') return '<span class="scrape-badge scrape-badge--partial">&#9888; 본문 일부</span>';
    if (article.scrape_status === 'failed') return '<span class="scrape-badge scrape-badge--failed" title="' + escapeHtml(article.scrape_error || '') + '">&#10007; ' + escapeHtml(article.scrape_error || '본문 실패') + '</span>';
    return '';
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
              ${scrapeStatusBadge(a)}
              <span>${escapeHtml(a.source)}</span>
              <span>${escapeHtml(a.keyword)}</span>
              <span>${escapeHtml(a.date)}</span>
              ${a.replacement_count > 0 ? `<span style="color:var(--color-text-muted);">교체 ${a.replacement_count}/${a.max_replacements}</span>` : ''}
            </div>
          </div>
          ${a.image_url ? '<img class="article-card__thumb" src="' + escapeHtml(a.image_url) + '" alt="" loading="lazy" onerror="this.classList.add(\'article-card__thumb--empty\')">' : ''}
        </div>
        <div class="article-card__toggle">
          <button class="article-card__toggle-btn" data-toggle="${a.index}">본문 펼치기 &#9662;</button>
        </div>
        <div class="article-card__body" id="body-${a.index}">
          <div class="article-card__body-inner">
            ${imgTag}
            ${escapeHtml(a.body_full || a.body_preview || '')}
            <div class="body-edit-trigger">
              <button class="btn btn--outline btn--sm article-card__edit-btn" data-edit-index="${a.index}">본문 편집</button>
            </div>
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

    // Accordion toggles (button)
    document.querySelectorAll('.article-card__toggle-btn').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        toggleAccordion(btn.dataset.toggle);
      });
    });

    // Header click -> accordion toggle (1-3)
    document.querySelectorAll('.article-card__header').forEach((header) => {
      header.addEventListener('click', (e) => {
        // Don't toggle if clicking checkbox or link
        if (e.target.closest('.article-card__checkbox') || e.target.closest('a')) return;
        const card = header.closest('.article-card');
        const idx = card.dataset.index;
        toggleAccordion(idx);
      });
    });

    // Select-all buttons (1-2)
    document.querySelectorAll('.select-all-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        const category = btn.dataset.category;
        toggleSelectAll(category, btn);
      });
    });

    // Edit buttons (2-4)
    document.querySelectorAll('.article-card__edit-btn').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        startBodyEdit(parseInt(btn.dataset.editIndex, 10));
      });
    });
  }

  function toggleAccordion(idx) {
    const bodyEl = document.getElementById('body-' + idx);
    const toggleBtn = document.querySelector(`[data-toggle="${idx}"]`);
    if (!bodyEl) return;
    const isOpen = bodyEl.classList.contains('open');
    bodyEl.classList.toggle('open');
    if (toggleBtn) {
      toggleBtn.innerHTML = isOpen ? '본문 펼치기 &#9662;' : '본문 접기 &#9652;';
    }
  }

  // ------------------------------------------------------------------
  // Category select all / deselect (1-2)
  // ------------------------------------------------------------------

  function toggleSelectAll(category, btn) {
    const containerMap = { main: cardsMain, other: cardsOther, market: cardsMarket };
    const container = containerMap[category];
    if (!container) return;

    const checkboxes = container.querySelectorAll('.article-card__checkbox:not(:disabled)');
    const allChecked = Array.from(checkboxes).every((cb) => cb.checked);

    checkboxes.forEach((cb) => {
      const idx = parseInt(cb.dataset.index, 10);
      if (allChecked) {
        // Deselect all
        cb.checked = false;
        selectedIndices.delete(idx);
        cb.closest('.article-card').classList.remove('selected');
      } else {
        // Select all
        cb.checked = true;
        selectedIndices.add(idx);
        cb.closest('.article-card').classList.add('selected');
      }
    });

    btn.textContent = allChecked ? '전체 선택' : '전체 해제';
    updateSelectedCount();
  }

  function resetSelectAllButtons() {
    document.querySelectorAll('.select-all-btn').forEach((btn) => {
      btn.textContent = '전체 선택';
    });
  }

  // ------------------------------------------------------------------
  // Sorting (2-2)
  // ------------------------------------------------------------------

  function sortArticles(list, sortBy) {
    const sorted = [...list];
    switch (sortBy) {
      case 'score-desc':
        sorted.sort((a, b) => b.score - a.score);
        break;
      case 'score-asc':
        sorted.sort((a, b) => a.score - b.score);
        break;
      case 'date-desc':
        sorted.sort((a, b) => (b.date || '').localeCompare(a.date || ''));
        break;
      default:
        // 'default' - use original order
        return null;
    }
    return sorted;
  }

  function handleSort(category, sortBy) {
    const containerMap = { main: cardsMain, other: cardsOther, market: cardsMarket };
    const container = containerMap[category];
    if (!container) return;

    // Save current selections
    const savedSelections = new Set(selectedIndices);

    let list;
    if (sortBy === 'default') {
      list = originalArticles[category] || [];
    } else {
      list = sortArticles(articles[category] || [], sortBy);
      if (!list) list = originalArticles[category] || [];
    }

    // Update the articles reference for this category
    articles[category] = list;

    // Re-render only this category
    container.innerHTML = renderCategoryCards(list);

    // Re-bind events for this container
    bindCardEventsInContainer(container);

    // Restore selection state
    container.querySelectorAll('.article-card__checkbox').forEach((cb) => {
      const idx = parseInt(cb.dataset.index, 10);
      if (savedSelections.has(idx)) {
        cb.checked = true;
        cb.closest('.article-card').classList.add('selected');
      }
    });
  }

  function bindCardEventsInContainer(container) {
    container.querySelectorAll('.article-card__checkbox').forEach((cb) => {
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

    container.querySelectorAll('.article-card__toggle-btn').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        toggleAccordion(btn.dataset.toggle);
      });
    });

    container.querySelectorAll('.article-card__header').forEach((header) => {
      header.addEventListener('click', (e) => {
        if (e.target.closest('.article-card__checkbox') || e.target.closest('a')) return;
        const card = header.closest('.article-card');
        toggleAccordion(card.dataset.index);
      });
    });

    // Bind edit buttons
    container.querySelectorAll('.article-card__edit-btn').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        startBodyEdit(parseInt(btn.dataset.editIndex, 10));
      });
    });
  }

  function resetSortSelects() {
    document.querySelectorAll('.sort-select').forEach((sel) => {
      sel.value = 'default';
    });
  }

  // Bind sort selects
  document.querySelectorAll('.sort-select').forEach((sel) => {
    sel.addEventListener('change', () => {
      handleSort(sel.dataset.category, sel.value);
    });
  });

  // ------------------------------------------------------------------
  // Inline body editing (2-4)
  // ------------------------------------------------------------------

  function startBodyEdit(index) {
    const bodyEl = document.getElementById('body-' + index);
    if (!bodyEl) return;

    // Find the article data
    const article = findArticleByIndex(index);
    if (!article) return;

    // Ensure accordion is open
    if (!bodyEl.classList.contains('open')) {
      toggleAccordion(String(index));
    }

    const inner = bodyEl.querySelector('.article-card__body-inner');
    const currentText = article.body_full || article.body_preview || '';

    inner.innerHTML = `
      <textarea class="body-edit-textarea" id="edit-textarea-${index}" rows="12">${escapeHtml(currentText)}</textarea>
      <div class="body-edit-actions">
        <button class="btn btn--primary btn--sm body-edit-save" data-save-index="${index}">저장</button>
        <button class="btn btn--outline btn--sm body-edit-cancel" data-cancel-index="${index}">취소</button>
      </div>
    `;

    // Focus textarea
    const textarea = document.getElementById('edit-textarea-' + index);
    textarea.focus();

    // Save handler
    inner.querySelector('.body-edit-save').addEventListener('click', async () => {
      const newText = textarea.value;
      try {
        await apiPatch(`/api/generate/${sessionId}/articles/${index}/body`, {
          body_full: newText,
        });
        // Update local data
        article.body_full = newText;
        article.body_preview = newText.substring(0, 200);
        previewLoaded = false;
        imagesLoaded = false;
        showToast('본문이 수정되었습니다.', 'success');
        // Re-render the body
        restoreBodyView(index, article);
      } catch (err) {
        showToast('본문 수정 실패: ' + err.message, 'error');
      }
    });

    // Cancel handler
    inner.querySelector('.body-edit-cancel').addEventListener('click', () => {
      restoreBodyView(index, article);
    });
  }

  function restoreBodyView(index, article) {
    const bodyEl = document.getElementById('body-' + index);
    if (!bodyEl) return;
    const inner = bodyEl.querySelector('.article-card__body-inner');
    const imgTag = article.image_local
      ? `<img class="article-card__image" src="/output/${article.image_local}" alt="" loading="lazy">`
      : (article.image_url ? `<img class="article-card__image" src="${escapeHtml(article.image_url)}" alt="" loading="lazy">` : '');

    inner.innerHTML = `
      ${imgTag}
      ${escapeHtml(article.body_full || article.body_preview || '')}
      <div class="body-edit-trigger">
        <button class="btn btn--outline btn--sm article-card__edit-btn" data-edit-index="${index}">본문 편집</button>
      </div>
    `;

    inner.querySelector('.article-card__edit-btn').addEventListener('click', (e) => {
      e.stopPropagation();
      startBodyEdit(index);
    });
  }

  function findArticleByIndex(index) {
    for (const cat of ['main', 'other', 'market']) {
      const found = (articles[cat] || []).find((a) => a.index === index);
      if (found) return found;
    }
    return null;
  }

  // ------------------------------------------------------------------
  // Category stats (2-1)
  // ------------------------------------------------------------------

  function updateCategoryStats() {
    const categories = { main: articles.main || [], other: articles.other || [], market: articles.market || [] };

    for (const [key, list] of Object.entries(categories)) {
      const statsEl = document.getElementById('stats-' + key);
      if (!statsEl || list.length === 0) {
        if (statsEl) statsEl.textContent = '';
        continue;
      }
      const scores = list.map((a) => a.score);
      const avg = Math.round(scores.reduce((s, v) => s + v, 0) / scores.length);
      const max = Math.max(...scores);
      statsEl.textContent = `(${list.length}건 / 평균 ${avg}점 / 최고 ${max}점)`;
    }
  }

  // ------------------------------------------------------------------
  // Selected count + action bar highlight
  // ------------------------------------------------------------------

  function updateSelectedCount() {
    const count = selectedIndices.size;
    selectedCountEl.textContent = `선택: ${count}건`;
    btnReplace.disabled = count === 0;

    // Action bar highlight
    if (count > 0) {
      selectedCountEl.classList.add('has-selection');
    } else {
      selectedCountEl.classList.remove('has-selection');
    }
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
      // Replace rendered images with placeholder text
      mdPreviewContent.querySelectorAll('img').forEach((img) => {
        const placeholder = document.createElement('span');
        placeholder.className = 'md-preview__image-placeholder';
        placeholder.textContent = '[이미지]';
        img.replaceWith(placeholder);
      });
      previewLoaded = true;
    } catch (err) {
      mdPreviewContent.innerHTML = `<p style="color:var(--color-danger);">미리보기를 불러올 수 없습니다: ${escapeHtml(err.message)}</p>`;
    }
  }

  // ------------------------------------------------------------------
  // Original Images (Tab 3)
  // ------------------------------------------------------------------

  let imagesLoaded = false;

  function loadImages() {
    if (imagesLoaded) return;

    const allArticles = [
      ...(articles.main || []),
      ...(articles.other || []),
      ...(articles.market || []),
    ];
    const withImages = allArticles.filter((a) => a.image_url || a.image_local);

    imagesCount.textContent = `이미지 ${withImages.length}건`;

    if (withImages.length === 0) {
      imagesGrid.innerHTML = '<div class="empty-state"><div class="empty-state__text">이미지 없음</div></div>';
      btnDownloadZip.disabled = true;
      imagesLoaded = true;
      return;
    }

    imagesGrid.innerHTML = withImages
      .map((a) => {
        const displaySrc = a.image_url ? escapeHtml(a.image_url) : `/output/${a.image_local}`;
        const downloadSrc = a.image_local ? `/output/${a.image_local}` : '';
        return `
          <div class="image-card">
            <img class="image-card__preview" src="${displaySrc}" alt="" loading="lazy"
                 onerror="this.style.display='none'">
            <div class="image-card__info">
              <div class="image-card__title" title="${escapeHtml(a.title)}">${escapeHtml(a.title)}</div>
              <div class="image-card__actions">
                ${downloadSrc ? `<a href="${downloadSrc}" download class="btn btn--outline btn--sm">다운로드</a>` : ''}
              </div>
            </div>
          </div>
        `;
      })
      .join('');

    imagesLoaded = true;
  }

  // ZIP download
  btnDownloadZip && btnDownloadZip.addEventListener('click', async () => {
    btnDownloadZip.disabled = true;
    btnDownloadZip.innerHTML = '<span class="spinner" style="width:14px;height:14px;border-width:2px;"></span> 다운로드 중...';

    try {
      const res = await fetch(`/api/generate/${sessionId}/images/zip`);
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: '다운로드 실패' }));
        throw new Error(err.detail || '다운로드 실패');
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
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
      btnDownloadZip.disabled = false;
      btnDownloadZip.textContent = '전체 다운로드 (ZIP)';
    }
  });

  // ------------------------------------------------------------------
  // Replacement flow
  // ------------------------------------------------------------------

  btnReplace.addEventListener('click', async () => {
    if (selectedIndices.size === 0) return;

    const indices = Array.from(selectedIndices);
    btnReplace.disabled = true;
    btnReplace.innerHTML = '<span class="spinner" style="width:14px;height:14px;border-width:2px;"></span> 교체 중...';

    // Optimistic UI: show loading overlay on selected cards
    indices.forEach((idx) => {
      const card = document.querySelector(`.article-card[data-index="${idx}"]`);
      if (card && !card.querySelector('.article-card__loading-overlay')) {
        const overlay = document.createElement('div');
        overlay.className = 'article-card__loading-overlay';
        overlay.innerHTML = '<span class="spinner" style="width:20px;height:20px;border-width:2px;"></span><span>교체 후보 검색 중...</span>';
        card.style.position = 'relative';
        card.appendChild(overlay);
      }
    });

    try {
      const data = await apiPost(`/api/generate/${sessionId}/replace`, {
        article_indices: indices,
      });
      pendingReplacements = data.replacements || [];
      showReplacementModal(indices);
    } catch (err) {
      showToast('기사 교체 실패: ' + err.message, 'error');
    } finally {
      // Remove loading overlays
      document.querySelectorAll('.article-card__loading-overlay').forEach((el) => el.remove());
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
      imagesLoaded = false;
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
      btnUpload.disabled = false;
      // Confirmed state UX
      btnConfirm.classList.add('btn--confirmed');
      btnConfirm.innerHTML = '&#10003; 저장 완료';
      btnUpload.classList.add('btn--pulse');
      document.querySelector('.action-bar').classList.add('action-bar--confirmed');
      // Disable all article checkboxes and replace button
      document.querySelectorAll('.article-card__checkbox').forEach(cb => { cb.disabled = true; });
      btnReplace.disabled = true;
    } catch (err) {
      showToast('저장 실패: ' + err.message, 'error');
      btnConfirm.disabled = false;
      btnConfirm.textContent = '확정 저장';
    }
  });

  // ------------------------------------------------------------------
  // Upload — Wiki URL modal (shared module)
  // ------------------------------------------------------------------

  const wikiUrlModal = document.getElementById('wiki-url-modal');

  const wikiModal = createWikiUrlModal({
    modalEl: wikiUrlModal,
    inputEl: document.getElementById('wiki-url-input'),
    errorEl: document.getElementById('wiki-url-error'),
    resolvedEl: document.getElementById('wiki-url-resolved'),
    resolvedNameEl: document.getElementById('resolved-page-name'),
    resolvedIdEl: document.getElementById('resolved-page-id'),
    recentSectionEl: document.getElementById('recent-pages-section'),
    recentListEl: document.getElementById('recent-pages-list'),
    verifyBtnEl: document.getElementById('btn-wiki-url-verify'),
    cancelBtnEl: document.getElementById('btn-wiki-url-cancel'),
    uploadBtnEl: document.getElementById('btn-wiki-url-upload'),
    onUpload: function (parentPageId, wikiId) {
      doUpload(parentPageId, wikiId);
    },
  });

  // 업로드 실행
  async function doUpload(parentPageId, wikiId) {
    wikiUrlModal.classList.add('hidden');
    btnUpload.disabled = true;
    btnUpload.innerHTML = '<span class="spinner" style="width:14px;height:14px;border-width:2px;"></span> 업로드 중...';

    try {
      const body = parentPageId ? { parent_page_id: parentPageId, wiki_id: wikiId } : undefined;
      const data = await apiPost(`/api/upload/${historyId}`, body);
      showToast('업로드 완료!', 'success');
      if (data.dooray_page_url) {
        completeMessage.innerHTML = 'Dooray 위키에 업로드되었습니다. <a href="' + escapeHtml(data.dooray_page_url) + '" target="_blank" rel="noopener" style="color:var(--color-primary); text-decoration:underline;">페이지 바로가기 &rarr;</a>';
      } else {
        completeMessage.textContent = 'Dooray 페이지에 업로드되었습니다. (Page ID: ' + (data.dooray_page_id || '-') + ')';
      }
      showState('complete');
    } catch (err) {
      showToast('업로드 실패: ' + err.message, 'error');
      btnUpload.disabled = false;
      btnUpload.textContent = '업로드';
    }
  }

  // 업로드 버튼 → 항상 모달 표시
  btnUpload.addEventListener('click', () => {
    if (!historyId) {
      showToast('먼저 확정 저장을 해주세요.', 'error');
      return;
    }
    wikiModal.show();
  });

  // ------------------------------------------------------------------
  // Keyboard shortcuts (3-2)
  // ------------------------------------------------------------------

  document.addEventListener('keydown', (e) => {
    // Escape -> close modals
    if (e.key === 'Escape') {
      if (!wikiUrlModal.classList.contains('hidden')) {
        wikiUrlModal.classList.add('hidden');
        return;
      }
      if (!replaceModal.classList.contains('hidden')) {
        btnReplaceCancel.click();
        return;
      }
    }

    // Ctrl+Enter -> confirm save
    if (e.ctrlKey && e.key === 'Enter' && !stateArticles.classList.contains('hidden')) {
      if (!btnConfirm.disabled) btnConfirm.click();
      return;
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
  // Idle state: week label + last generation info
  // ------------------------------------------------------------------

  function getCurrentWeekLabel() {
    const today = new Date();
    const day = today.getDay();
    const monday = new Date(today);
    monday.setDate(today.getDate() - (day === 0 ? 6 : day - 1));
    const weekOfMonth = Math.floor((monday.getDate() - 1) / 7) + 1;
    return `${monday.getMonth() + 1}월 ${weekOfMonth}주차`;
  }

  const idleWeekTitle = document.getElementById('idle-week-title');
  const idleLastInfo = document.getElementById('idle-last-info');

  if (idleWeekTitle) {
    idleWeekTitle.textContent = `${getCurrentWeekLabel()} 이커머스 뉴스`;
  }

  // Load last generation info
  async function loadLastGenerationInfo() {
    try {
      const data = await apiGet('/api/history/recent');
      const items = Array.isArray(data) ? data : (data.items || []);
      if (items.length > 0) {
        const last = items[0];
        const label = formatWeekLabel(last.week_range);
        idleLastInfo.textContent = `마지막 생성: ${label} · ${last.article_count}건 · ${last.status}`;
        idleLastInfo.classList.remove('hidden');
      }
    } catch { /* silent */ }
  }

  loadLastGenerationInfo();

  // ------------------------------------------------------------------
  // Init
  // ------------------------------------------------------------------

  btnStart.addEventListener('click', () => {
    startGeneration();
  });

  // Auto-start if ?autostart=1
  if (new URLSearchParams(window.location.search).get('autostart') === '1') {
    history.replaceState(null, '', window.location.pathname);
    startGeneration();
  }
})();
