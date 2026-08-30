(function () {
  "use strict";

  var settled = false;

  function showFailure(detail) {
    if (settled) {
      return;
    }
    var root = document.getElementById("root");
    if (!root || root.childElementCount > 0) {
      settled = true;
      return;
    }
    settled = true;
    root.innerHTML =
      '<main style="font-family:system-ui,sans-serif;padding:32px;color:#18211f">' +
      '<p style="font-size:12px;font-weight:700;letter-spacing:.12em">CONTROLSTUDIO UI RECOVERY</p>' +
      '<h1 style="font-size:28px">화면을 시작하지 못했습니다</h1>' +
      '<p>Host와 XG5000 작업은 다시 실행하지 않았습니다. 화면 시작 오류를 확인해 복구해야 합니다.</p>' +
      '<pre style="white-space:pre-wrap;padding:12px;background:#eef0e9;border-radius:8px">' +
      String(detail || "프런트엔드가 제한 시간 안에 시작되지 않았습니다.")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;") +
      "</pre></main>";
  }

  window.addEventListener("error", function (event) {
    showFailure(event && event.message ? event.message : "JavaScript 시작 오류");
  });
  window.addEventListener("unhandledrejection", function (event) {
    var reason = event && event.reason;
    showFailure(reason && reason.message ? reason.message : String(reason || "비동기 시작 오류"));
  });
  window.setTimeout(function () {
    showFailure("프런트엔드가 제한 시간 안에 시작되지 않았습니다.");
  }, 3000);
})();
