# ControlStudio Web

ControlStudio Host의 React·TypeScript 표시 클라이언트다. XG5000 래더 편집기를
복제하거나 벤더 작업을 직접 실행하지 않는다.

## 개발

이 디렉터리에서 실행한다.

```powershell
npm ci
npm run dev
npm run build
```

Vite는 `127.0.0.1:5173`에서 개발 UI를 제공하고 `/api`를 개발 Host
`127.0.0.1:8767`로 전달한다. 제품 작업에는 호환되는 실행 중 Host와 Launcher가
발급한 유효한 브라우저 세션이 필요하다. Vite 실행만으로 Host가 시작되지는 않는다.
제품 빌드는 Host가 설정한 same-origin loopback 주소에서 제공한다.

웹은 공개 `/api/v1` 계약과 인증된 SSE 이벤트를 사용한다. 실행 상태의 기준은
Host 상태와 실제 작업 결과다. 브라우저 새로고침·닫기로 변경 요청을 재실행하지 않는다.
PWA 메타데이터를 제공하며 service worker는 사용하지 않는다.

내부 제품 명세와 운영 검증 기록은 별도로 관리한다. 구성요소 준비 사항과 공개 계약
안내는 [Platform README](../README.md)를 따른다.
