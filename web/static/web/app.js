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
