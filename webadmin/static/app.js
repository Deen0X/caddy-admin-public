/* Comportamiento de la consola Caddy Admin (vanilla). */
(function () {
  'use strict';

  var toastBox = document.getElementById('toast');

  function toast(message, kind) {
    if (!toastBox) return;
    var el = document.createElement('div');
    el.className = kind || 'ok';
    el.textContent = message;
    toastBox.appendChild(el);
    setTimeout(function () {
      el.style.opacity = '0';
      setTimeout(function () { el.remove(); }, 250);
    }, 3200);
  }
  window.DNXtoast = toast;

  /* Confirmación en formularios marcados con data-confirm. */
  document.addEventListener('submit', function (event) {
    var form = event.target;
    if (!(form instanceof HTMLFormElement)) return;
    var message = form.getAttribute('data-confirm');
    if (message && !window.confirm(message)) {
      event.preventDefault();
      return;
    }
    var button = form.querySelector('button[type="submit"]');
    if (button && !button.hasAttribute('data-keep')) {
      setTimeout(function () { button.disabled = true; }, 0);
    }
  });

  /* Vista previa del avatar (emoji o URL). */
  var avatarInput = document.getElementById('avatar-input');
  var avatarPreview = document.getElementById('avatar-preview');
  if (avatarInput && avatarPreview) {
    var updateAvatar = function () {
      var value = (avatarInput.value || '').trim();
      if (/^https?:\/\//i.test(value)) {
        avatarPreview.innerHTML = '<img src="' + value.replace(/"/g, '&quot;') + '" alt="">';
        avatarPreview.classList.add('has-img');
      } else {
        avatarPreview.textContent = value || '👤';
        avatarPreview.classList.remove('has-img');
      }
    };
    avatarInput.addEventListener('input', updateAvatar);
    updateAvatar();
  }

  /* Actualización: comprobar versión contra GitHub. */
  var updateBtn = document.getElementById('update-check-btn');
  if (updateBtn) {
    updateBtn.addEventListener('click', function () {
      var button = updateBtn;
      button.disabled = true;
      fetch('/api/update/check', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: '{}'
      })
        .then(function (r) { return r.ok ? r.json() : Promise.reject(r.status); })
        .then(function (data) {
          if (window.DNXtoast) {
            if (data.detail) {
              window.DNXtoast('No se pudo comprobar: ' + data.detail, 'err');
            } else {
              window.DNXtoast('Comprobación OK · local ' + (data.current_commit || '—') + ' · GitHub ' + (data.latest_commit || data.latest || '—') + '.', 'ok');
            }
          }
          window.location.reload();
        })
        .catch(function () {
          if (window.DNXtoast) window.DNXtoast('No se pudo completar la comprobación de actualizaciones.', 'err');
          button.disabled = false;
        });
    });
  }

  /* Actualización: aplicar la versión disponible (rebuild + restart del contenedor). */
  var updateNowBtn = document.getElementById('update-now');
  var waitRestart = function () {
    var tries = 0;
    var timer = setInterval(function () {
      tries += 1;
      fetch('/health', { credentials: 'same-origin', cache: 'no-store' })
        .then(function (r) {
          if (r.ok) {
            clearInterval(timer);
            window.location.reload();
          }
        })
        .catch(function () { /* la consola se está reiniciando */ });
      if (tries > 150) clearInterval(timer);
    }, 2000);
  };
  if (updateNowBtn) {
    updateNowBtn.addEventListener('click', function () {
      var button = updateNowBtn;
      button.disabled = true;
      var overlay = document.getElementById('global-loading');
      if (overlay) {
        var label = overlay.querySelector('span');
        if (label) label.textContent = 'Aplicando actualización… la consola se reiniciará';
        overlay.classList.add('show');
      }
      fetch('/api/update/apply', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: '{}'
      })
        .then(function (r) { return r.json().then(function (data) { return { ok: r.ok, data: data }; }); })
        .then(function (res) {
          if (res.ok) {
            if (window.DNXtoast) window.DNXtoast('Actualización en marcha. Esperando a que vuelva la consola…', 'ok');
            waitRestart();
          } else {
            if (overlay) overlay.classList.remove('show');
            if (window.DNXtoast) window.DNXtoast('No se pudo aplicar la actualización: ' + (res.data.error || 'error'), 'err');
            button.disabled = false;
          }
        })
        .catch(function () {
          /* Si el contenedor se reinició a mitad de la petición, esperamos y recargamos. */
          waitRestart();
        });
    });
  }

  /* Estadísticas en vivo. */
  var statsRoot = document.getElementById('stats-live');
  if (statsRoot) {
    var fmt = function (n) {
      n = Number(n || 0);
      return n.toLocaleString('es-ES');
    };
    var setText = function (id, value) {
      var el = document.getElementById(id);
      if (el) el.textContent = value;
    };
    var renderBars = function (samples) {
      var box = document.getElementById('stats-bars');
      if (!box) return;
      var max = 1;
      samples.forEach(function (s) { max = Math.max(max, Number(s.admin_requests || 0) + Number(s.requests || 0)); });
      box.innerHTML = samples.map(function (s) {
        var total = Number(s.admin_requests || 0) + Number(s.requests || 0);
        var h = Math.max(2, Math.round((total / max) * 100));
        return '<div class="col" title="' + fmt(total) + ' peticiones · ' + (s.ts || '') + '"><i style="height:' + h + '%"></i></div>';
      }).join('') || '<div class="empty">Sin muestras todavía.</div>';
      setText('stats-max', fmt(max));
    };
    var refresh = function () {
      fetch('/api/stats', { credentials: 'same-origin' })
        .then(function (r) { return r.ok ? r.json() : Promise.reject(r.status); })
        .then(function (data) {
          var live = data.live || {};
          var metrics = live.metrics || {};
          setText('m-requests', fmt(metrics.requests));
          setText('m-admin', fmt(metrics.admin_requests));
          setText('m-errors', fmt(metrics.errors));
          setText('m-routes', live.routes_total + ' (' + live.routes_enabled + ' activas)');
          var disk = live.disk || {};
          var freeGb = (Number(disk.free || 0) / 1073741824).toFixed(1);
          var totalGb = (Number(disk.total || 0) / 1073741824).toFixed(1);
          setText('m-disk', freeGb + ' GB libres');
          setText('m-disk-sub', 'de ' + totalGb + ' GB');
          var pct = live.routes_total ? Math.round((live.routes_enabled / live.routes_total) * 100) : 0;
          var donut = document.getElementById('donut');
          if (donut) { donut.style.setProperty('--pct', pct); }
          setText('donut-center', pct + '%');
          renderBars((data.samples || []).slice().reverse());
          setText('stats-updated', 'actualizado ' + new Date().toLocaleTimeString('es-ES'));
        })
        .catch(function () { /* silencio: se reintenta en el siguiente ciclo */ });
    };
    refresh();
    setInterval(refresh, 20000);
    document.addEventListener('dnx:refresh', refresh);
  }

  /* Rueda de carga para páginas que tardan en responder (diagnóstico). */
  var globalLoading = document.getElementById('global-loading');
  function hideLoading() {
    if (globalLoading) globalLoading.classList.remove('show');
  }
  window.addEventListener('pageshow', hideLoading);
  document.addEventListener('click', function (event) {
    if (event.defaultPrevented || event.button !== 0) return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    var link = event.target.closest('a');
    if (!link || link.target === '_blank') return;
    var href = link.getAttribute('href') || '';
    if (href === '/diagnostico' || href.indexOf('/diagnostico?') === 0) {
      if (globalLoading) globalLoading.classList.add('show');
    }
  });
})();
