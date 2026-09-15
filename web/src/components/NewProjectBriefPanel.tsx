import { useEffect, useState } from "react";
import { hostApi } from "../api/client";
import type { KnowledgeSource } from "../api/types";
import { Icon } from "./Icon";

export interface NewProjectBrief {
  supplementalRequirements?: string;
  projectName: string;
  machinePurpose: string;
  sequence: string;
  hardwareAndIo: string;
  safetyAndInterlocks: string;
  operatingModes: string;
  performanceAndAcceptance: string;
  attachmentSourceIds: string[];
}

interface NewProjectBriefPanelProps {
  initialBrief?: NewProjectBrief;
  busy: boolean;
  nativeProjectAvailable: boolean;
  onCancel: () => void;
  onManageKnowledge: () => void;
  onSubmit: (brief: NewProjectBrief) => void;
}

const ATTACHMENT_KIND_LABELS: Record<
  "manual" | "standard" | "project_document",
  string
> = {
  manual: "매뉴얼",
  standard: "표준",
  project_document: "프로젝트 문서",
};

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
      label: "프로젝트 설정",
      available: nativeProjectAvailable,
      unavailableLabel: "현재 환경에서 비활성",
    },
    {
      number: 4,
      label: "프로그램 생성·검사",
      available: nativeProjectAvailable,
      unavailableLabel: "현재 환경에서 비활성",
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
  nativeProjectAvailable,
  onCancel,
  onManageKnowledge,
  onSubmit,
}: NewProjectBriefPanelProps) {
  const [supplementalRequirements, setSupplementalRequirements] = useState(
    initialBrief?.supplementalRequirements ?? "",
  );
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
  const [attachmentSourceIds, setAttachmentSourceIds] = useState<string[]>(
    initialBrief?.attachmentSourceIds ?? [],
  );
  const [attachmentSources, setAttachmentSources] = useState<KnowledgeSource[]>([]);
  const [attachmentLoading, setAttachmentLoading] = useState(false);
  const [attachmentError, setAttachmentError] = useState("");
  const [attachmentRefresh, setAttachmentRefresh] = useState(0);
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

  useEffect(() => {
    const controller = new AbortController();
    setAttachmentLoading(true);
    setAttachmentError("");
    void hostApi
      .listKnowledgeSources({
        kinds: ["manual", "standard", "project_document"],
        statuses: ["confirmed"],
        limit: 200,
        signal: controller.signal,
      })
      .then((page) => setAttachmentSources(page.items))
      .catch((error: unknown) => {
        if (!controller.signal.aborted) {
          setAttachmentError(
            error instanceof Error
              ? error.message
              : "첨부 가능한 자료를 불러오지 못했습니다.",
          );
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setAttachmentLoading(false);
        }
      });
    return () => controller.abort();
  }, [attachmentRefresh]);

  const availableAttachmentIds = new Set(
    attachmentSources.map((source) => source.sourceId),
  );
  const missingAttachmentSourceIds = attachmentSourceIds.filter(
    (sourceId) => !availableAttachmentIds.has(sourceId),
  );

  return (
    <section
      className="new-project-brief"
      aria-labelledby="new-project-brief-title"
    >
      <div className="project-flow-heading">
        <div>
          <h2 id="new-project-brief-title">어떤 설비를 만들까요?</h2>
          <p>
            만들고 싶은 동작과 지켜야 할 조건을 알려 주세요. 모르는 선택 항목은 비워도 됩니다.
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

      <ProjectFlowStepper
        activeStep={1}
        nativeProjectAvailable={nativeProjectAvailable}
      />

      <form
        className="new-project-brief-form"
        onSubmit={(event) => {
          event.preventDefault();
          setSafetyTouched(true);
          if (!briefReady) {
            return;
          }
          onSubmit({
            supplementalRequirements,
            projectName: projectName.trim(),
            machinePurpose: machinePurpose.trim(),
            sequence: sequence.trim(),
            hardwareAndIo: hardwareAndIo.trim(),
            safetyAndInterlocks: safetyAndInterlocks.trim(),
            operatingModes: operatingModes.trim(),
            performanceAndAcceptance: performanceAndAcceptance.trim(),
            attachmentSourceIds,
          });
        }}
      >
        {initialBrief?.supplementalRequirements && (
          <details>
            <summary>대화에서 추가한 요구사항</summary>
            <label>
              <span>추가 답변을 확인하거나 정리할 수 있습니다.</span>
              <textarea
                value={supplementalRequirements}
                onChange={(event) => setSupplementalRequirements(event.target.value)}
                rows={6}
                maxLength={20_000}
                disabled={busy}
              />
            </label>
          </details>
        )}
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

        <details className="brief-optional-details" open={missingAttachmentSourceIds.length > 0 || undefined}>
          <summary>참고 자료 첨부{attachmentSourceIds.length ? ` · ${attachmentSourceIds.length}개 선택` : " · 선택"}</summary>
        <fieldset className="brief-attachments">
          <legend>계획에 참고 자료 첨부</legend>
          <div className="brief-attachment-heading">
            <p>
              계획에 참고할 매뉴얼이나 설비 문서를 선택하세요.
            </p>
            <div>
              <button
                className="button secondary-button"
                type="button"
                disabled={busy}
                onClick={onManageKnowledge}
              >
                <Icon name="database" size={15} />
                자료 관리
              </button>
              <button
                className="button secondary-button"
                type="button"
                disabled={busy || attachmentLoading}
                onClick={() => setAttachmentRefresh((value) => value + 1)}
              >
                <Icon name="refresh" size={15} />
                새로고침
              </button>
            </div>
          </div>

          {attachmentError ? (
            <p className="brief-attachment-error" role="alert">
              {attachmentError}
            </p>
          ) : null}

          <div className="brief-attachment-list" aria-busy={attachmentLoading}>
            {attachmentSources.map((source) => {
              const selected = attachmentSourceIds.includes(source.sourceId);
              const kindLabel =
                ATTACHMENT_KIND_LABELS[
                  source.kind as keyof typeof ATTACHMENT_KIND_LABELS
                ] ?? source.kind;
              return (
                <label key={source.sourceId}>
                  <input
                    type="checkbox"
                    checked={selected}
                    disabled={
                      busy || (!selected && attachmentSourceIds.length >= 20)
                    }
                    onChange={(event) =>
                      setAttachmentSourceIds((current) =>
                        event.target.checked
                          ? [...current, source.sourceId]
                          : current.filter((item) => item !== source.sourceId),
                      )
                    }
                  />
                  <Icon name="file" size={17} />
                  <span>
                    <strong>{source.title}</strong>
                    <small>
                      {kindLabel} · {source.scope}
                      {source.vendor ? ` · ${source.vendor}` : ""}
                      {source.productFamily
                        ? ` · ${source.productFamily}`
                        : ""}
                      {source.version ? ` · v${source.version}` : ""}
                    </small>
                  </span>
                </label>
              );
            })}

            {missingAttachmentSourceIds.map((sourceId) => (
              <label className="brief-attachment-missing" key={sourceId}>
                <input
                  type="checkbox"
                  checked
                  disabled={busy}
                  onChange={() =>
                    setAttachmentSourceIds((current) =>
                      current.filter((item) => item !== sourceId),
                    )
                  }
                />
                <Icon name="warning" size={17} />
                <span>
                  <strong>현재 사용할 수 없는 첨부 자료</strong>
                  <small>선택을 해제하거나 자료 관리에서 다시 확인해 주세요.</small>
                </span>
              </label>
            ))}

            {!attachmentLoading &&
            attachmentSources.length === 0 &&
            missingAttachmentSourceIds.length === 0 ? (
              <p>
                첨부 가능한 확인 자료가 없습니다. 자료 관리에서 문서를 가져온 뒤
                새로고침하세요.
              </p>
            ) : null}
            {attachmentLoading ? <p>첨부 가능한 자료를 불러오는 중…</p> : null}
          </div>
          <small>{attachmentSourceIds.length}/20개 선택</small>
        </fieldset>
        </details>

        <div className="new-project-brief-note">
          <Icon name="shield" size={17} />
          <span>
            먼저 계획을 만듭니다. 아직 프로젝트 파일이나 PLC는 변경하지 않습니다.
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
