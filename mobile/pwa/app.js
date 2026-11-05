/* 应急食品 paimind — mobile PWA client.
 *
 * Talks to the same HTTP API the desktop app uses.  The mobile build keeps the
 * external-engine modes hidden by default (builtin only) per the spec, and exposes
 * them behind the developer tab.
 */
(function () {
  'use strict';

  // The API normally lives on the same origin as the page (e.g. served by the
  // app itself).  When the PWA is opened from a dev server or a file:// URL, the
  // address must be overridable — the desktop app also lets the user point at a
  // LAN machine, so the same override is honoured here.
  var API = (function () {
    var saved = null;
    try { saved = localStorage.getItem('paimind.api'); } catch (e) { /* file:// */ }
    if (saved && /^https?:\/\//i.test(saved)) return saved.replace(/\/+$/, '');
    return window.location.origin;
  })();
  var state = { tiers: [], engines: {}, hf: null, onlineConsent: false };

  // ---------------------------------------------------------------- helpers
  function $(id) { return document.getElementById(id); }
  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  async function api(path, options) {
    options = options || {};
    options.headers = Object.assign({ 'Content-Type': 'application/json' }, options.headers || {});
    if (options.body && typeof options.body !== 'string') {
      options.body = JSON.stringify(options.body);
    }
    var res = await fetch(API + path, options);
    var data = null;
    try { data = await res.json(); } catch (e) { data = null; }
    if (!res.ok) {
      var err = new Error((data && data.error && data.error.message) || res.statusText);
      err.status = res.status;
      err.code = data && data.error && data.error.code;
      err.details = data && data.error && data.error.details;
      throw err;
    }
    return data;
  }
  function debounce(fn, ms) {
    var t;
    return function () {
      clearTimeout(t);
      t = setTimeout(fn, ms);
    };
  }

  // ---------------------------------------------------------------- options
  function addOption(text) {
    text = (text || '').trim();
    if (!text) return;
    var list = $('options');
    var li = el('li');
    var span = el('span', 'txt', text);
    var del = el('span', 'del', '×');
    del.addEventListener('click', function () { li.remove(); syncButtons(); });
    li.appendChild(span);
    li.appendChild(del);
    list.appendChild(li);
    syncButtons();
  }
  function currentOptions() {
    return Array.prototype.map.call($('options').children, function (li, i) {
      return { id: 'opt' + (i + 1), text: li.querySelector('.txt').textContent };
    });
  }
  function syncButtons() {
    var ok = $('question').value.trim().length > 0 && $('options').children.length >= 2;
    $('btnDecide').disabled = !ok;
    $('btnAgain').disabled = !ok;
    $('btnOnline').disabled = !ok;
    var hint = '';
    if (!$('question').value.trim()) hint = '问题不能为空';
    else if ($('options').children.length < 2) hint = '至少需要 2 个选项';
    $('optionsHint').textContent = hint;
  }

  // ---------------------------------------------------------------- decide
  function resetResult() {
    $('selected').textContent = '尚未决策';
    $('reason').textContent = '';
    $('confidence').textContent = '';
    $('badge').textContent = '本地';
    $('badge').className = 'badge';
    $('alternatives').innerHTML = '';
    $('sources').innerHTML = '';
    $('altWrap').hidden = true;
    $('srcWrap').hidden = true;
  }

  async function decide(forceOnline) {
    var question = $('question').value.trim();
    var options = currentOptions();
    if (!question || options.length < 2) { syncButtons(); return; }

    $('btnDecide').disabled = true;
    $('btnAgain').disabled = true;
    $('btnOnline').disabled = true;
    $('status').textContent = '推理中…';

    try {
      var body = await api('/v1/decide', {
        method: 'POST',
        body: {
          question: question,
          options: options,
          mode: forceOnline ? 'online' : 'auto',
          model_tier: 'auto',
        },
      });
      renderResult(body);
      $('status').textContent = '完成 · ' + body.latency_ms + ' ms';
    } catch (err) {
      $('status').textContent = '';
      if (err.code === 'MODEL_NOT_READY') {
        showModal('模型未就绪', '该档位还没下载。\n\n请到「设置 → 档位」里选择下载源并下载。\n\n' + (err.details && err.details.path ? '目录：' + err.details.path : ''));
      } else if (err.code === 'MODEL_UNSUPPORTED') {
        showModal('档位不可用', err.message + '\n\n需要：\n' +
          ((err.details && err.details.hardware && JSON.stringify(err.details.hardware.gpus)) || ''));
      } else {
        showModal('决策失败', err.message);
      }
    } finally {
      $('btnDecide').disabled = false;
      $('btnAgain').disabled = false;
      $('btnOnline').disabled = false;
    }
  }

  function renderResult(body) {
    var sel = body.selected || {};
    $('selected').textContent = sel.text || '';
    $('reason').textContent = body.reason || '';
    $('confidence').textContent = '置信度 ' + Math.round((sel.confidence || 0) * 100) + '%';

    var sourceMap = { local: '本地', online: '联网', hybrid: '本地+联网' };
    $('badge').textContent = sourceMap[body.source] || body.source;
    $('badge').className = 'badge' + (body.source === 'local' ? '' : ' online');

    var alts = body.alternatives || [];
    if (alts.length) {
      $('alternatives').innerHTML = '';
      alts.forEach(function (a) {
        var row = el('div', 'alt');
        row.appendChild(el('span', null, a.text));
        row.appendChild(el('span', 'score', Math.round(a.score * 100) + '%'));
        $('alternatives').appendChild(row);
      });
      $('altWrap').hidden = false;
    }

    var srcs = body.sources || [];
    if (srcs.length) {
      $('sources').innerHTML = '';
      srcs.forEach(function (s) {
        var row = el('div', 'src');
        var link = el('a', null, (s.title || '') + '  (' + (s.domain || '') + ')');
        link.href = s.url;
        link.target = '_blank';
        link.rel = 'noopener';
        link.style.color = 'inherit';
        row.appendChild(link);
        $('sources').appendChild(row);
      });
      $('srcWrap').hidden = false;
    }
  }

  // ---------------------------------------------------------------- settings
  async function loadSettings() {
    try {
      var models = await api('/v1/models?refresh=true');
      state.tiers = models.tiers || [];
      renderTiers(models);
      renderHardware(models.hardware || {});
      $('chipTier').textContent = '档位 ' + (models.current_tier || '-');
      $('chipBackend').textContent = models.current_mode || '内置';
    } catch (e) {
      $('hardware').innerHTML = '读取失败：' + e.message;
    }
    try {
      var sources = await api('/v1/download-sources');
      renderSources(sources);
    } catch (e) { /* 下载源读取失败不阻塞页面 */ }
    try {
      state.hf = await api('/v1/hf/auth/status');
      renderHf(state.hf);
    } catch (e) { /* 忽略 */ }
    try {
      var health = await api('/health');
      $('chipApi').textContent = health.status === 'ok' ? 'API 运行中' : 'API -';
    } catch (e) {
      $('chipApi').textContent = 'API 未启动';
    }
  }

  function renderTiers(models) {
    var box = $('tierCards');
    box.innerHTML = '';
    (models.tiers || []).forEach(function (t) {
      var card = el('div', 'tier' + (t.status === 'disabled' ? ' disabled' : ''));
      var head = el('div', 'tier-head');
      head.appendChild(el('span', 'tier-name', t.tier + ' · ' + t.params));
      head.appendChild(el('span', 'tier-state ' + t.status, { ready: '可用', warning: '性能警告', disabled: '不可用' }[t.status]));
      card.appendChild(head);
      card.appendChild(el('div', 'tier-meta',
        '磁盘 ' + (t.disk_mb / 1000).toFixed(1) + ' GB · 峰值内存 ' + t.peak_memory_gb.toFixed(1) + ' GB' +
        (t.current ? ' · 使用中' : '')));
      if (t.status === 'disabled') {
        card.appendChild(el('div', 'tier-reason', t.unavailable_reason || '硬件不达标'));
      } else if (t.status === 'warning') {
        card.appendChild(el('div', 'tier-reason', t.warning_reason || ''));
      } else {
        var btn = el('button', 'btn small', t.downloaded ? '切换加载' : '下载');
        btn.addEventListener('click', function () { tierAction(t); });
        card.appendChild(btn);
      }
      box.appendChild(card);
    });
  }

  function renderHardware(hw) {
    var gpus = (hw.gpus || []).map(function (g) { return g.name + ' (' + g.vram_gb + 'GB)'; }).join('、') || '无';
    $('hardware').innerHTML =
      '<div>可用内存 <b>' + (hw.available_memory_gb || 0).toFixed(1) + ' GB</b></div>' +
      '<div>磁盘剩余 <b>' + (hw.free_disk_gb || 0).toFixed(0) + ' GB</b></div>' +
      '<div>AVX2 <b>' + (hw.avx2 ? '支持' : '不支持') + '</b></div>' +
      '<div>GPU <b>' + gpus + '</b></div>';
  }

  function renderSources(payload) {
    var box = $('sources');
    box.innerHTML = '';
    (payload.groups || []).forEach(function (group) {
      (group.mirrors || []).forEach(function (m) {
        var row = el('div', 'source' + (m.kind === 'custom' ? ' custom' : '') + (group.provider === 'huggingface' ? ' hf' : ''));
        var left = el('div');
        left.appendChild(el('div', null, m.name));
        left.appendChild(el('div', 'tag', (m.kind === 'builtin' ? '内置' : '自定义') + (m.auth_required ? ' · 需鉴权' : '')));
        row.appendChild(left);
        var test = el('button', 'btn small', '测试');
        test.addEventListener('click', async function () {
          test.textContent = '…';
          try {
            var r = await api('/v1/download-sources/' + group.provider + '/' + m.id + '/test', { method: 'POST' });
            test.textContent = r.ok ? '通' : '不通';
            test.style.color = r.ok ? 'var(--ready)' : 'var(--danger)';
            if (!r.ok) showModal('镜像不可用', m.name + '\n' + r.message);
          } catch (e) {
            test.textContent = '失败';
          }
        });
        row.appendChild(test);
        box.appendChild(row);
      });
    });
  }

  function renderHf(hf) {
    var box = $('hfBox');
    if (!hf.logged_in) {
      box.innerHTML = '<div>未登录 Hugging Face</div><div>上传模型需要 Write Token</div>';
      var btn = el('button', 'btn small', '登录 Hugging Face');
      btn.style.marginTop = '8px';
      btn.addEventListener('click', promptHfLogin);
      box.appendChild(btn);
      return;
    }
    box.innerHTML =
      '<div>已登录 <b>' + (hf.username || '') + '</b></div>' +
      '<div>write 权限：<b>' + (hf.token_write_scope === true ? '有' : hf.token_write_scope === false ? '无' : '未知') + '</b></div>';
    var out = el('button', 'btn small', '退出登录');
    out.style.marginTop = '8px';
    out.addEventListener('click', async function () {
      await api('/v1/hf/auth/logout', { method: 'POST' });
      loadSettings();
    });
    box.appendChild(out);
  }

  function promptHfLogin() {
    showModal('登录 Hugging Face', '请输入 Write Token（huggingface.co/settings/tokens）', [
      { label: '取消', value: null },
      {
        label: '登录', value: async function (input) {
          var token = (input || '').trim();
          if (!token) return 'token 不能为空';
          try {
            var r = await api('/v1/hf/auth/login', { method: 'POST', body: { token: token } });
            showModal('已登录', (r.username || '') + (r.token_write_scope === false ? '\n注意：token 可能缺少 write 权限' : ''));
            loadSettings();
            return null;
          } catch (e) { return e.message; }
        }, secret: true,
      },
    ]);
  }

  async function tierAction(tier) {
    if (tier.downloaded) {
      try {
        await api('/v1/models/' + tier.tier + '/activate', { method: 'POST' });
        showModal('已切换', tier.tier + ' 已设为当前档位');
        loadSettings();
      } catch (e) { showModal('切换失败', e.message); }
      return;
    }
    showModal('下载 ' + tier.tier, '请到桌面版选择下载源后下载（GitHub / Hugging Face 双主源，支持多镜像降级）。');
  }

  // ---------------------------------------------------------------- modal
  function showModal(title, body, actions) {
    $('modalTitle').textContent = title;
    $('modalBody').textContent = body || '';
    var box = $('modalActions');
    box.innerHTML = '';
    var input = null;
    var needsInput = actions && actions.some(function (a) { return a.secret; });
    if (needsInput) {
      input = el('input');
      input.type = 'password';
      input.placeholder = 'token';
      input.style.marginTop = '8px';
      $('modalBody').appendChild(input);
    }
    var list = actions || [{ label: '知道了', value: null }];
    list.forEach(function (a) {
      var btn = el('button', 'btn' + (a.secret ? ' primary' : ''), a.label);
      btn.addEventListener('click', async function () {
        if (a.value) {
          var err = await a.value(input ? input.value : undefined);
          if (err) { $('modalBody').textContent = err; return; }
        }
        $('modal').hidden = true;
      });
      box.appendChild(btn);
    });
    $('modal').hidden = false;
  }

  // ---------------------------------------------------------------- tabs
  function switchPage(id) {
    Array.prototype.forEach.call(document.querySelectorAll('.page'), function (p) {
      p.classList.toggle('active', p.id === id);
    });
    Array.prototype.forEach.call(document.querySelectorAll('.tab[data-page]'), function (t) {
      t.classList.toggle('active', t.getAttribute('data-page') === id);
    });
    if (id === 'page-settings') loadSettings();
  }

  function init() {
    $('optionAdd').addEventListener('click', function () {
      addOption($('optionInput').value);
      $('optionInput').value = '';
      $('optionInput').focus();
    });
    $('optionInput').addEventListener('keydown', function (e) {
      if (e.key === 'Enter') { e.preventDefault(); $('optionAdd').click(); }
    });
    $('question').addEventListener('input', debounce(syncButtons, 80));
    $('btnDecide').addEventListener('click', function () { decide(false); });
    $('btnAgain').addEventListener('click', function () { resetResult(); decide(false); });
    $('btnOnline').addEventListener('click', function () { decide(true); });
    $('btnClear').addEventListener('click', function () {
      $('question').value = '';
      $('options').innerHTML = '';
      resetResult();
      syncButtons();
    });
    $('btnRefreshHw').addEventListener('click', loadSettings);
    Array.prototype.forEach.call(document.querySelectorAll('.tab[data-page]'), function (t) {
      t.addEventListener('click', function () { switchPage(t.getAttribute('data-page')); });
    });
    $('devToggle').addEventListener('click', function () {
      $('devCard').hidden = !$('devCard').hidden;
      if (!$('devCard').hidden) $('devApi').focus();
    });
    // 浅色/深色切换 / light-dark toggle
    (function () {
      var isLight = function () {
        return document.documentElement.getAttribute('data-theme') === 'light';
      };
      var sync = function () {
        $('themeToggle').textContent = isLight() ? '深色' : '浅色';
        var meta = document.querySelector('meta[name="theme-color"]');
        if (meta) meta.setAttribute('content', isLight() ? '#F4F6FA' : '#14161B');
      };
      sync();
      $('themeToggle').addEventListener('click', function () {
        if (isLight()) {
          document.documentElement.removeAttribute('data-theme');
          localStorage.setItem('paimind.theme', 'dark');
        } else {
          document.documentElement.setAttribute('data-theme', 'light');
          localStorage.setItem('paimind.theme', 'light');
        }
        sync();
      });
    })();
    // The API base is resolved once at startup, so saving it reloads the page.
    $('devApiSave').addEventListener('click', function () {
      var url = $('devApi').value.trim().replace(/\/+$/, '');
      if (url && !/^https?:\/\//i.test(url)) {
        showModal('地址无效', 'API 地址必须以 http:// 或 https:// 开头。');
        return;
      }
      localStorage.setItem('paimind.api', url);
      showModal('已保存', url
        ? 'API 地址已保存：' + url + '\n即将重新加载…'
        : '已恢复为「与页面同源」，即将重新加载…');
      setTimeout(function () { location.reload(); }, 900);
    });
    var savedApi = null;
    try { savedApi = localStorage.getItem('paimind.api'); } catch (e) { /* file:// */ }
    if (savedApi) $('devApi').value = savedApi;
    $('devOllamaSave').addEventListener('click', function () {
      var url = $('devOllama').value.trim();
      localStorage.setItem('paimind.dev.ollama', url);
      showModal('已保存', '局域网 Ollama 地址已保存：' + (url || '(清空)'));
    });
    var saved = localStorage.getItem('paimind.dev.ollama');
    if (saved) $('devOllama').value = saved;

    addOption('近郊露营');
    addOption('周边古镇一日游');
    syncButtons();
    loadSettings();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
