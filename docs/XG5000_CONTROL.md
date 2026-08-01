# XG5000 제어 및 LD 분석 기술 명세

검증 환경: Windows 11, XG5000 V4.81 및 V4.82.1, XG-SIM

이 문서는 첫 provider adapter의 기술 명세다. 초기 LS 제품에서 XG5000은
래더와 벤더 프로젝트의 시각적 원본이며, ControlStudio 웹 GUI는 이를 범용
래더 IDE로 복제하지 않는다. ControlStudio는 분석 근거와 프로그램·네트워크
위치를 구조화해 표시하고 XG5000의 해당 위치를 연다.

화면에 원하는 래더가 보인다는 사실은 변경 성공 증거가 아니다. 성공 판정은
native payload·artifact hash, 구조 diff, 프로그램 검사, 저장·독립 재열기,
XG-SIM 또는 controller readback처럼 adapter가 반환한 독립 증거를 사용한다.

## 1. 결론

XG5000은 공개된 범용 자동화 API만으로 모든 편집 기능을 제공하지 않는다.
현재 검증된 제어는 다음 세 계층을 조합한다.

1. Win32 창·메뉴·툴바·대화상자 제어
2. XG5000 전용 클립보드 형식
   - IL: `XG5000MNE`
   - LD: `XG5000LADDER`
3. XG-SIM 런타임 메모리: x86 `XimUtil.DeviceInterface`

절대 화면좌표는 최후 수단으로만 사용한다. 기본 식별자는 PID/HWND, 창 클래스,
컨트롤 ID, MDI 부모 관계이며 좌표를 사용할 때도 확인된 자식 컨트롤 내부의
상대 위치만 사용한다.

XG5000 UI 자동화는 XG5000과 같은 로그인 사용자·interactive desktop·권한
수준에서만 수행한다. 잠금 화면, Session 0, 다른 RDP 세션 또는 integrity
level 불일치를 확실히 판별하지 못하면 UI 작업을 시작하지 않는다.

## 2. XG5000LADDER

### 확인된 구조

네이티브 페이로드는 다음 정보를 포함한다.

- PLC명과 프로그램명 UTF-16LE 헤더
- 행 스트림 시작 마커 `FF FE FF FF`
- 탭으로 구분된 요소 레코드
- 시각 행 기준 좌표: 행마다 1024 증가
- 접점 코드
  - `6`: NO
  - `7`: NC
  - `8`: 상승 에지
  - `9`: 하강 에지
- 비교 요소 `33`
- 출력 하위형식
  - `14`: OUT
  - `16`: SET
  - `17`: RST
  - `33`: 응용명령

구현은 [ladder.py](../mcp/src/xg5000_control/ladder.py)에 있다.
해석하지 않은 배선 토큰도 각 행의 `raw_tokens`에 보존한다.

### 현재 해석 범위

현재 디코더는 프로그램명, 시각 행, 접점, 비교식, 출력 명령과 좌표를 복원한다.
병렬 가지의 시작점과 합류점을 논리 그래프로 만드는 기능은 다음 구현 단계다.
따라서 현재 출력은 “행별 구조”이며, 모든 런을 자동으로 완전한 불리언 식으로
변환했다고 주장하지 않는다.

## 3. XG5000MNE

`XG5000MNE`는 제한된 시험 프로그램 작성과 기존 E2E 경로에 사용한다.
CPU나 프로젝트 언어 설정에 따라 IL/MNE를 사용할 수 없으므로 일반 프로젝트 분석의
기본 경로로 사용하지 않는다. 일반 분석의 기준은 LD다.

안전 규칙:

- 빈 시험 프로그램에서만 삽입
- 허용 명령과 주소 문법 검사
- `END` 보장
- 삽입 후 전용 클립보드 exact readback
- 기존 프로그램이면 쓰기 거부

## 4. 프로그램 검사와 저장

- XG5000의 공식 `프로그램 검사` 명령 사용
- 성공 문구와 오류 개수 0을 모두 확인
- MDI 편집기 닫기로 프로젝트 모델에 변경 반영
- 저장 전후 프로젝트 SHA-256 기록
- 별도 XG5000 프로세스에서 재열기 후 readback

경고는 오류와 구분해 기록한다. 경고가 있으면 통과 여부와 별개로 개수와 메시지를
증거에 남긴다.

## 5. XG-SIM

프로그램 다운로드와 런타임 메모리 쓰기는 다른 작업이다.

1. XG-SIM 시작
2. XimUtil 연결로 서버 준비 확인
3. XG5000 공식 `쓰기` 실행
4. 쓰기 완료 문구 확인
5. RUN 전환
6. 입력 비트만 변경하고 출력 추종 확인
7. 성공·실패와 관계없이 원래 메모리로 복원

XimUtil 정보:

| 항목 | 값 |
|---|---|
| ProgID | `XimUtil.DeviceInterface` |
| bitness | x86 in-process COM |
| 브리지 | `mcp/tools/xgsim_bridge_x86.cs` |

## 6. 제한된 제품 작업 경로

### 6.1 네이티브 신규 프로젝트

`project.create_native`는 Host가 선택한 exact XG5000 PID/HWND와 기존의 빈
workspace directory를 대상으로 네이티브 `프로젝트 > 새 프로젝트` dialog를
연다. 절대 화면 좌표가 아니라 dialog label, control identity와 결과
project title/path/hash를 검증한다. project name, PLC series, CPU model,
첫 program name/language와 description은 exact plan digest와 일회성 Host
authorization에 묶인다.

이 경로는 자동화된 fake backend에서 검증됐다. 현재 실제 XG5000 시도는 visible
modal과 elevation 불일치 때문에 첫 UI mutation 전에 안전하게 거부됐다.
따라서 live 생성 성공, XG5000 검사·저장·독립 재열기나 요구사항 기반 I/O·태스크·
심볼·일반 LD 구성은 아직 증명되지 않았다. 이 작업은 PLC에 연결하거나 쓰지
않는다.

### 6.2 오프라인 한 operand transaction

제품 Host는 동일 길이 LD operand 한 개에 한해 exact target·plan digest·
만료 사용자 승인과 MCP 일회성 HMAC authorization을 결합한다. MCP는
snapshot·backup·plan·apply·diff·XG5000 check·save checkpoint와 프로젝트
범위 durable lease를 기록한다.

중단 복구는 자동 재적용하지 않는다. apply 전이면 `ABORTED_NO_MUTATION`,
apply 이후이면 현재 payload가 exact original/planned/neither인지 확인해
no-op rollback, 한 번의 역변경 또는 `QUARANTINED`로 닫는다. 새 Host 제품
경로는 자동 crash tests를 통과했지만 실제 XG5000 전체 E2E와 외부 재열기·
XG-SIM은 아직 수행하지 않았다.

### 6.3 LS FEnet 읽기 전용

`controller.ls.status`와 `controller.ls.read`는 XGT FEnet TCP 2004의 상태와
1~16개 device를 읽는 별도 read-only 경로다. RFC1918 IPv4, CGNAT와 IPv6 ULA
숫자 주소만 허용하고 loopback, link-local, public address와 DNS hostname은
거부한다. 반환 identity는 provisional이며 write에 충분하지 않다.

현재 증거는 fake TCP server다. 실제 PLC, project upload/복원, STEP·timer·
scan·trace는 검증하지 않았고 write/run edit/force/mode/download 명령은 없다.

### 6.4 XG5000 project identity 보증

현재 설치에서는 XG5000 COM activation/ROT PID readback을 medium-integrity
Host에서 얻을 수 없다. 따라서 XG5000 0개 상태에서 ControlStudio가 새로 실행한
정확한 PID/HWND, process creation time, native Open, exact title과 working-copy
path/hash를 같은 MCP 수명 안에서 묶은 `controlled_launch_read_only` 보증만
실기 검증됐다.

이 보증은 Read·Analyze에만 유효하다.
`project_identity_verified_against_selected_pid=false`를 유지하며 mutation,
save, simulation과 online capability를 열지 않는다. 해당 기능에는 제한된
elevated x86 broker가 COM PID/HWND/path/hash readback을 제공하고 별도 실기
검증을 통과해야 한다.

## 7. 확인된 한계

- 실제 PLC download/write/run edit/force/mode 변경은 현재 구현·활성화돼 있지
  않으며 현장 검증도 수행하지 않았다.
- XG5000 버전·언어·DPI에 따라 UI 구조가 달라질 수 있다.
- V4.81도 전체 XG-SIM E2E를 통과했다. V4.82.1은 현재 검증 환경이지
  필수 최소 버전이 아니다.
- 일부 대화상자는 64비트 Python에서 텍스트가 깨지므로 컨트롤 ID fallback이 필요하다.
- LD 전용 형식은 공식 공개 사양이 아니므로 관찰되지 않은 요소는 보수적으로 거부해야 한다.
- 프로그램의 기계적 의미는 I/O 주석이나 전장 도면이 없으면 주소 기준까지만 확정 가능하다.
