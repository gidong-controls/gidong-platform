import { useState } from "react";
import { Icon } from "./Icon";

export interface NewProjectBrief {
  projectName: string;
  machinePurpose: string;
  sequence: string;
  hardwareAndIo: string;
  safetyAndInterlocks: string;
  operatingModes: string;
  performanceAndAcceptance: string;
}

interface NewProjectBriefPanelProps {
  initialBrief?: NewProjectBrief;
  busy: boolean;
  onCancel: () => void;
  onSubmit: (brief: NewProjectBrief) => void;
}

export function ProjectFlowStepper({
  activeStep,
  nativeProjectAvailable = false,
}: {
  activeStep: 1 | 2 | 3 | 4;
  nativeProjectAvailable?: boolean;
}) {
  const steps = [
    { number: 1, label: "요구사항", available: true, unavailableLabel: "" },
    { number: 2, label: "계획 검토", available: true, unavailableLabel: "" },
    {
      number: 3,
      label: "XG5000 기술 구성",
      available: nativeProjectAvailable,
      unavailableLabel: "현재 환경에서 비활성",
    },
    {
      number: 4,
      label: "로직·검사·시뮬레이션",
      available: false,
      unavailableLabel: "아직 구현되지 않음",
    },
  ] as const;

  return (
    <ol className="project-flow-steps" aria-label="새 프로젝트 진행 단계">
      {steps.map((step) => (
        <li
          key={step.number}
          className={[
            step.number === activeStep ? "project-step-active" : "",
            step.number < activeStep ? "project-step-complete" : "",
            !step.available ? "project-step-planned" : "",
          ]
            .filter(Boolean)
            .join(" ")}
          aria-current={step.number === activeStep ? "step" : undefined}
        >
          <span>{step.number}</span>
          <strong>{step.label}</strong>
          {!step.available && <small>{step.unavailableLabel}</small>}
        </li>
      ))}
    </ol>
  );
}

export function NewProjectBriefPanel({
  initialBrief,
  busy,
  onCancel,
  onSubmit,
}: NewProjectBriefPanelProps) {
  const [projectName, setProjectName] = useState(
    initialBrief?.projectName ?? "",
  );
  const [machinePurpose, setMachinePurpose] = useState(
    initialBrief?.machinePurpose ?? "",
  );
  const [sequence, setSequence] = useState(initialBrief?.sequence ?? "");
  const [hardwareAndIo, setHardwareAndIo] = useState(
    initialBrief?.hardwareAndIo ?? "",
  );
  const [safetyAndInterlocks, setSafetyAndInterlocks] = useState(
    initialBrief?.safetyAndInterlocks ?? "",
  );
  const [operatingModes, setOperatingModes] = useState(
    initialBrief?.operatingModes ?? "",
  );
  const [performanceAndAcceptance, setPerformanceAndAcceptance] = useState(
    initialBrief?.performanceAndAcceptance ?? "",
  );
  const [safetyTouched, setSafetyTouched] = useState(false);
  const [optionalDetailsOpen, setOptionalDetailsOpen] = useState(
    Boolean(
      initialBrief?.sequence ||
        initialBrief?.hardwareAndIo ||
        initialBrief?.operatingModes ||
        initialBrief?.performanceAndAcceptance,
    ),
  );
  const projectNameReady = Boolean(projectName.trim());
  const machinePurposeReady = Boolean(machinePurpose.trim());
  const safetyReady = Boolean(safetyAndInterlocks.trim());
  const briefReady =
    projectNameReady && machinePurposeReady && safetyReady && !busy;

  return (
    <section
      className="new-project-brief"
      aria-labelledby="new-project-brief-title"
    >
      <div className="project-flow-heading">
        <div>
          <p className="eyebrow">NEW PROJECT · PLANNING FIRST</p>
          <h2 id="new-project-brief-title">어떤 설비를 만들까요?</h2>
          <p>
            CPU부터 고르는 대신 설비의 목적과 제약을 먼저 정리합니다. 모르는
            선택 항목은 비워도 되며, 에이전트가 중요한 질문만 이어서 확인합니다.
          </p>
        </div>
        <button
          className="icon-button"
          type="button"
          aria-label="새 프로젝트 요구사항 닫기"
          disabled={busy}
          onClick={onCancel}
        >
          <Icon name="close" size={18} />
        </button>
      </div>

      <ProjectFlowStepper activeStep={1} />

      <form
        className="new-project-brief-form"
        onSubmit={(event) => {
          event.preventDefault();
          setSafetyTouched(true);
          if (!briefReady) {
            return;
          }
          onSubmit({
            projectName: projectName.trim(),
            machinePurpose: machinePurpose.trim(),
            sequence: sequence.trim(),
            hardwareAndIo: hardwareAndIo.trim(),
            safetyAndInterlocks: safetyAndInterlocks.trim(),
            operatingModes: operatingModes.trim(),
            performanceAndAcceptance: performanceAndAcceptance.trim(),
          });
        }}
      >
        <label>
          <span>프로젝트 이름</span>
          <input
            type="text"
            value={projectName}
            maxLength={80}
            autoComplete="off"
            disabled={busy}
            placeholder="예: 대동 인덱스 개선"
            onChange={(event) => setProjectName(event.target.value)}
          />
        </label>

        <label className="brief-primary-question">
          <span>이 설비가 무엇을 해야 하나요?</span>
          <textarea
            value={machinePurpose}
            maxLength={4000}
            rows={5}
            disabled={busy}
            placeholder="제품이 들어온 뒤 검사하고, 합격품은 배출하고 불량품은 별도 위치로 보내는 설비입니다."
            onChange={(event) => setMachinePurpose(event.target.value)}
          />
          <small>필수 · 구현하려는 결과를 작업자 관점에서 적어 주세요.</small>
        </label>

        <label className="brief-wide">
          <span>안전·인터록·운영 제약 · 필수</span>
          <textarea
            value={safetyAndInterlocks}
            maxLength={4000}
            rows={4}
            required
            aria-label="안전·인터록·운영 제약"
            aria-invalid={safetyTouched && !safetyReady}
            aria-describedby="safety-and-interlocks-requirement"
            disabled={busy}
            placeholder="예: 도어가 열리면 축 정지, 클램프 확인 전 이송 금지, 수동·자동 모드 필요"
            onBlur={() => setSafetyTouched(true)}
            onChange={(event) => {
              setSafetyAndInterlocks(event.target.value);
              if (event.target.value.trim()) {
                setSafetyTouched(true);
              }
            }}
          />
          <small
            id="safety-and-interlocks-requirement"
            className={
              safetyTouched && !safetyReady
                ? "brief-field-requirement brief-field-error"
                : "brief-field-requirement"
            }
            role={safetyTouched && !safetyReady ? "alert" : undefined}
          >
            {safetyTouched && !safetyReady
              ? "계획을 시작하려면 안전 조건이나 현재 확인되지 않은 위험을 반드시 적어 주세요."
              : "필수 · 확인된 조건이 없다면 “현재 확인된 안전 조건 없음 · 현장 위험성 평가 필요”라고 적어 주세요."}
          </small>
        </label>

        <details
          className="brief-optional-details"
          open={optionalDetailsOpen}
          onToggle={(event) =>
            setOptionalDetailsOpen(event.currentTarget.open)
          }
        >
          <summary>알고 있는 정보 추가</summary>
          <div>
            <label>
              <span>동작 순서·공정</span>
              <textarea
                value={sequence}
                maxLength={4000}
                rows={4}
                disabled={busy}
                placeholder="예: 원점 확인 → 제품 감지 → 클램프 → 검사 → 배출"
                onChange={(event) => setSequence(event.target.value)}
              />
            </label>

            <label>
              <span>하드웨어·I/O·통신</span>
              <textarea
                value={hardwareAndIo}
                maxLength={4000}
                rows={4}
                disabled={busy}
                placeholder="예: LS XGK, 실린더 2개, 포토센서 3개, 인버터 1대. 아직 미정이어도 됩니다."
                onChange={(event) => setHardwareAndIo(event.target.value)}
              />
            </label>

            <label>
              <span>운전·초기화·복구 모드</span>
              <textarea
                value={operatingModes}
                maxLength={4000}
                rows={4}
                disabled={busy}
                placeholder="예: 자동·수동·보전, 원점 복귀, 비상정지 해제 뒤 작업자 재시작. 모르면 비워도 됩니다."
                onChange={(event) => setOperatingModes(event.target.value)}
              />
            </label>

            <label>
              <span>성능·품질·인수 기준</span>
              <textarea
                value={performanceAndAcceptance}
                maxLength={4000}
                rows={4}
                disabled={busy}
                placeholder="예: 사이클 6초 이내, 불량 분리 확인, 정상·경계·고장·복구 시나리오 통과."
                onChange={(event) =>
                  setPerformanceAndAcceptance(event.target.value)
                }
              />
            </label>
          </div>
        </details>

        <div className="new-project-brief-note">
          <Icon name="shield" size={17} />
          <span>
            이 단계에서는 XG5000, 프로젝트 파일, PLC를 변경하지 않습니다.
            먼저 요구사항과 안전 조건을 검토 가능한 계획으로 만듭니다.
          </span>
        </div>

        <div className="new-project-brief-actions">
          <button
            className="button secondary-button"
            type="button"
            disabled={busy}
            onClick={onCancel}
          >
            취소
          </button>
          <button
            className="button primary-button"
            type="submit"
            disabled={!briefReady}
          >
            {busy ? "계획 대화 준비 중…" : "계획서 초안 만들기"}
          </button>
        </div>
      </form>
    </section>
  );
}
