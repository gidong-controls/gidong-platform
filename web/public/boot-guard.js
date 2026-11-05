(function () {
  "use strict";

  var settled = false;
  var retryKey = "controlstudio.uiBootRecoveryAttempt";
  var watchdog;
  var recoveryTimer;
  var observer;

  function clearRetryAttempt() {
    try {
      window.sessionStorage.removeItem(retryKey);
    } catch (_) {
      // Storage may be unavailable; manual screen recovery still works.
    }
  }

  function finishBoot() {
    var root = document.getElementById("root");
    if (!root || root.childElementCount === 0 || document.getElementById("controlstudio-boot-failure")) {
      return false;
    }
    settled = true;
    window.clearTimeout(watchdog);
    window.clearTimeout(recoveryTimer);
    if (observer) observer.disconnect();
    window.removeEventListener("error", onError);
    window.removeEventListener("unhandledrejection", onRejection);
    window.removeEventListener("controlstudio:ui-mounted", finishBoot);
    clearRetryAttempt();
    return true;
  }

  function claimAutomaticRetry() {
    try {
      if (window.sessionStorage.getItem(retryKey) === "1") return false;
      window.sessionStorage.setItem(retryKey, "1");
      return true;
    } catch (_) {
      // Without a durable per-tab budget, do not risk a reload loop.
      return false;
    }
  }

  function showFailure(detail) {
    if (finishBoot()) {
      return;
    }
    if (settled) {
      return;
    }
    var root = document.getElementById("root");
    if (!root) {
      return;
    }
    settled = true;
    root.innerHTML =
      '<main id="controlstudio-boot-failure" style="font-family:system-ui,sans-serif;padding:32px;color:#18211f">' +
      '<p style="font-size:12px;font-weight:700;letter-spacing:.12em">CONTROLSTUDIO UI RECOVERY</p>' +
      '<h1 style="font-size:28px">화면을 시작하지 못했습니다</h1>' +
      '<p>Host와 XG5000 작업은 다시 실행하지 않았습니다. 화면 시작 오류를 확인해 복구해야 합니다.</p>' +
      '<pre style="white-space:pre-wrap;padding:12px;background:#eef0e9;border-radius:8px">' +
      String(detail || "프런트엔드가 제한 시간 안에 시작되지 않았습니다.")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;") +
      '</pre><button id="controlstudio-boot-retry" type="button">화면 다시 불러오기</button></main>';

    document.getElementById("controlstudio-boot-retry").addEventListener("click", function () {
      clearRetryAttempt();
      window.location.reload();
    });
    if (claimAutomaticRetry()) {
      // Retry only the not-yet-mounted page. A late React commit cancels this;
      // a second boot failure leaves the manual button instead of looping.
      recoveryTimer = window.setTimeout(function () {
        if (!finishBoot()) window.location.reload();
      }, 250);
    }
  }

  function onError(event) {
    showFailure(event && event.message ? event.message : "JavaScript 시작 오류");
  }
  function onRejection(event) {
    var reason = event && event.reason;
    showFailure(reason && reason.message ? reason.message : String(reason || "비동기 시작 오류"));
  }
  var root = document.getElementById("root");
  if (root) {
    observer = new MutationObserver(finishBoot);
    observer.observe(root, { childList: true });
  }
  window.addEventListener("controlstudio:ui-mounted", finishBoot);
  window.addEventListener("error", onError);
  window.addEventListener("unhandledrejection", onRejection);
  watchdog = window.setTimeout(function () {
    showFailure("프런트엔드가 제한 시간 안에 시작되지 않았습니다.");
  }, 3000);
})();
