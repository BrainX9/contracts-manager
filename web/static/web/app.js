// Version 0.10.0: Fenster mit den Neuigkeiten nach einem Update. Es öffnet
// sich einmal; Schließen (Knopf, X, Klick daneben, Esc) gilt als gesehen.
document.addEventListener('DOMContentLoaded', function () {
  var element = document.getElementById('whats-new');
  if (!element || !window.bootstrap) {
    return;
  }
  var sent = false;
  element.addEventListener('hidden.bs.modal', function () {
    if (sent) {
      return;
    }
    sent = true;
    var token = document.querySelector('input[name=csrfmiddlewaretoken]');
    var headers = JSON.parse(document.body.getAttribute('hx-headers') || '{}');
    headers['X-Requested-With'] = 'fetch';
    if (token && !headers['X-CSRFToken']) {
      headers['X-CSRFToken'] = token.value;
    }
    fetch(element.getAttribute('data-seen-url'), {
      method: 'POST', headers: headers, credentials: 'same-origin', keepalive: true
    });
  });
  window.bootstrap.Modal.getOrCreateInstance(element).show();
});

// Version 0.12.0: Klappfelder mit Kästchen in der Vertragsliste. Ohne
// JavaScript funktionieren sie auch (<details>); hier nur Komfort:
// Beschriftung sofort anpassen, Klick daneben und Esc schließen.
document.addEventListener('change', function (event) {
  var details = event.target.closest && event.target.closest('details.multi-filter');
  if (!details) {
    return;
  }
  var checked = details.querySelectorAll('input[type=checkbox]:checked');
  var text = details.getAttribute('data-all-label');
  if (checked.length === 1) {
    var label = details.querySelector('label[for="' + checked[0].id + '"]');
    text = label ? label.textContent : text;
  } else if (checked.length > 1) {
    text = details.getAttribute('data-many-label').replace('__count__', checked.length);
  }
  details.querySelector('.multi-filter-summary').textContent = text;
});

function closeMultiFilters(except) {
  document.querySelectorAll('details.multi-filter[open]').forEach(function (details) {
    if (details !== except) {
      details.removeAttribute('open');
    }
  });
}

document.addEventListener('click', function (event) {
  closeMultiFilters(event.target.closest && event.target.closest('details.multi-filter'));
});

document.addEventListener('keydown', function (event) {
  if (event.key === 'Escape') {
    var open = document.querySelector('details.multi-filter[open]');
    closeMultiFilters(null);
    if (open) {
      open.querySelector('summary').focus();
    }
  }
});

// Menü rechtsbündig, wenn es sonst rechts aus dem Bild ragt
document.addEventListener('toggle', function (event) {
  var details = event.target;
  if (!details.matches || !details.matches('details.multi-filter') || !details.open) {
    return;
  }
  var menu = details.querySelector('.multi-filter-menu');
  menu.classList.remove('align-end');
  if (menu.getBoundingClientRect().right > document.documentElement.clientWidth - 8) {
    menu.classList.add('align-end');
  }
}, true);
