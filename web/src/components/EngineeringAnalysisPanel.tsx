import type {
  EngineeringAnalysisArtifact,
  EngineeringConfidence,
  EngineeringCoverage,
  EngineeringCoverageArea,
  EngineeringCoverageStatus,
  EngineeringHypothesis,
  EngineeringHypothesisV4,
  EngineeringLocation,
  EngineeringLocationAccess,
  EngineeringLocationSemanticStatus,
  EngineeringProgram,
  EngineeringProjectStructureFact,
  EngineeringProjectStructureKind,
  EngineeringRelation,
  EngineeringRelationKind,
} from "../api/types";

export interface EngineeringAnalysisPanelProps {
  analysis: EngineeringAnalysisArtifact;
  digest?: string;
  programs: EngineeringProgram[];
  focusAvailable: boolean;
  focusBusyProgramId?: string;
  onFocusLocation: (programId: string, location: EngineeringLocation) => void;
}

const CONFIDENCE_LABEL: Record<EngineeringConfidence, string> = {
  high: "높음",
  medium: "중간",
  low: "낮음",
};

const HYPOTHESIS_VERIFICATION_LABEL = {
  open: "검증 대기",
  verified: "검증됨",
  rejected: "기각됨",
} as const;

const ACCESS_LABEL: Record<EngineeringLocationAccess, string> = {
  read: "읽기",
  write: "쓰기",
  state_write: "상태 쓰기",
  condition: "조건",
  action: "동작",
  argument: "인수",
  unknown: "미확인",
};

const LOCATION_STATUS_LABEL: Record<EngineeringLocationSemanticStatus, string> = {
  observed: "관측됨",
  structured: "구조화됨",
  partial: "일부 구조화",
};

const RELATION_LABEL: Record<EngineeringRelationKind, string> = {
  read: "읽기 관계",
  write: "쓰기 관계",
  state_write: "상태 변경",
  condition: "조건 관계",
  causal_path: "인과 경로",
  state_transition: "상태 전이",
};

const COVERAGE_AREA_LABEL: Record<EngineeringCoverageArea, string> = {
  programs: "프로그램",
  logic_graph: "로직 그래프",
  cross_references: "상호 참조",
  state_model: "상태 모델",
  causal_paths: "인과 경로",
  hardware: "하드웨어",
  runtime: "실행 상태",
  external_integrations: "외부 연동",
  project_structure: "프로젝트 구조",
};

const PROJECT_STRUCTURE_KIND_LABEL: Record<EngineeringProjectStructureKind, string> = {
  configuration: "구성",
  task: "태스크",
  code_unit: "코드 유닛",
  symbol: "심볼",
  device: "디바이스",
  call_edge: "호출 관계",
  parameter: "파라미터",
  module: "모듈",
  communication: "통신",
};

const COVERAGE_STATUS_LABEL: Record<EngineeringCoverageStatus, string> = {
  complete: "조사 완료",
  partial: "일부 조사",
  not_observed: "관측되지 않음",
  not_applicable: "해당 없음",
};

type UniqueProgramMap = ReadonlyMap<string, EngineeringProgram | null>;

function isV4Hypothesis(
  value: EngineeringHypothesis | EngineeringHypothesisV4,
): value is EngineeringHypothesisV4 {
  return "verification" in value;
}

function buildUniqueProgramMap(programs: EngineeringProgram[]): UniqueProgramMap {
  const result = new Map<string, EngineeringProgram | null>();
  for (const program of programs) {
    result.set(program.name, result.has(program.name) ? null : program);
  }
  return result;
}

function EvidenceRefs({ values }: { values: string[] }) {
  if (values.length === 0) {
    return null;
  }
  return (
    <details className="engineering-analysis-technical">
      <summary>참조 기록 {values.length}개</summary>
    <ul className="engineering-analysis-evidence" aria-label="참조 기록 식별자">
      {values.map((value) => (
        <li key={value}>
          <code>{value}</code>
        </li>
      ))}
    </ul>
    </details>
  );
}

function DetailList({ label, values }: { label: string; values: string[] }) {
  if (values.length === 0) {
    return null;
  }
  return (
    <div className="engineering-analysis-detail">
      <strong>{label}</strong>
      <ul>
        {values.map((value, index) => (
          <li key={`${label}-${index}-${value}`}>{value}</li>
        ))}
      </ul>
    </div>
  );
}

function TechnicalValue({ label, value }: { label: string; value?: string | number }) {
  if (value === undefined) {
    return null;
  }
  return (
    <div>
      <dt>{label}</dt>
      <dd>
        <code>{value}</code>
      </dd>
    </div>
  );
}

interface LocationCardProps {
  location: EngineeringLocation;
  uniquePrograms: UniqueProgramMap;
  focusAvailable: boolean;
  focusBusyProgramId?: string;
  onFocusLocation: (programId: string, location: EngineeringLocation) => void;
  compact?: boolean;
}

function LocationCard({
  location,
  uniquePrograms,
  focusAvailable,
  focusBusyProgramId,
  onFocusLocation,
  compact = false,
}: LocationCardProps) {
  const matchedProgram = uniquePrograms.get(location.program);
  const canFocus =
    focusAvailable &&
    Boolean(matchedProgram) &&
    location.visualRow !== undefined &&
    location.tokenOffset !== undefined;
  const isOpening = matchedProgram?.programId === focusBusyProgramId;
  const focusBusy = Boolean(focusBusyProgramId);
  const positionParts = [
    location.networkIndex !== undefined
      ? `네트워크 ${location.networkIndex}`
      : undefined,
    location.visualRow !== undefined ? `화면 행 ${location.visualRow}` : undefined,
  ].filter((value): value is string => value !== undefined);

  return (
    <article
      className={`engineering-analysis-location${compact ? " is-compact" : ""}`}
    >
      <div className="engineering-analysis-location-heading">
        <div>
          <strong>{location.program}</strong>
          {location.device && <code>{location.device}</code>}
          {positionParts.length > 0 && <small>{positionParts.join(" · ")}</small>}
        </div>
        <div className="engineering-analysis-structure-badges">
          <span>{ACCESS_LABEL[location.access]}</span>
          <span className={`semantic-${location.semanticStatus}`}>
            {LOCATION_STATUS_LABEL[location.semanticStatus]}
          </span>
        </div>
      </div>

      {canFocus && matchedProgram && (
        <button
          type="button"
          className="engineering-analysis-focus"
          disabled={focusBusy}
          aria-busy={isOpening || undefined}
          aria-label={`${location.program} XG5000에서 열기`}
          onClick={() => onFocusLocation(matchedProgram.programId, location)}
        >
          {isOpening ? "여는 중…" : "XG5000에서 열기"}
        </button>
      )}

      <details className="engineering-analysis-technical">
        <summary>기술 식별 정보</summary>
        <dl>
          <TechnicalValue label="evidenceRef" value={location.evidenceRef} />
          <TechnicalValue label="networkId" value={location.networkId} />
          <TechnicalValue label="rungId" value={location.rungId} />
          <TechnicalValue label="tokenOffset" value={location.tokenOffset} />
        </dl>
      </details>
    </article>
  );
}

interface RelationCardProps {
  relation: EngineeringRelation;
  uniquePrograms: UniqueProgramMap;
  focusAvailable: boolean;
  focusBusyProgramId?: string;
  onFocusLocation: (programId: string, location: EngineeringLocation) => void;
}

function RelationCard({
  relation,
  uniquePrograms,
  focusAvailable,
  focusBusyProgramId,
  onFocusLocation,
}: RelationCardProps) {
  return (
    <li className="engineering-analysis-relation">
      <div className="engineering-analysis-relation-heading">
        <div className="engineering-analysis-signal-path">
          <code>{relation.source}</code>
          <span aria-hidden="true">→</span>
          <code>{relation.target}</code>
        </div>
        <div className="engineering-analysis-structure-badges">
          <span>{RELATION_LABEL[relation.relationKind]}</span>
          <span>{relation.direct ? "직접" : "간접"}</span>
          <span className={`semantic-${relation.semanticStatus}`}>
            {relation.semanticStatus === "structured" ? "구조화됨" : "일부 구조화"}
          </span>
        </div>
      </div>

      {relation.deviceChain.length > 0 && (
        <p className="engineering-analysis-device-chain">
          <span>신호 흐름</span>
          {relation.deviceChain.map((device, index) => (
            <span key={`${device}-${index}`}>
              {index > 0 && <span aria-hidden="true">→</span>}
              <code>{device}</code>
            </span>
          ))}
        </p>
      )}

      {relation.locations.length > 0 && (
        <details className="engineering-analysis-related-locations">
          <summary>연결된 근거 위치 {relation.locations.length}곳</summary>
          <div className="engineering-analysis-location-list">
            {relation.locations.map((location, index) => (
              <LocationCard
                key={`${location.evidenceRef}-${location.program}-${index}`}
                location={location}
                uniquePrograms={uniquePrograms}
                focusAvailable={focusAvailable}
                focusBusyProgramId={focusBusyProgramId}
                onFocusLocation={onFocusLocation}
                compact
              />
            ))}
          </div>
        </details>
      )}

      <details className="engineering-analysis-technical">
        <summary>기술 식별 정보</summary>
        <dl>
          <TechnicalValue label="evidenceRef" value={relation.evidenceRef} />
          <TechnicalValue label="relationKind" value={relation.relationKind} />
        </dl>
      </details>
    </li>
  );
}

function CoverageCard({ coverage }: { coverage: EngineeringCoverage }) {
  const hasCount =
    coverage.observedCount !== undefined || coverage.totalCount !== undefined;
  return (
    <li className="engineering-analysis-coverage-card">
      <div className="engineering-analysis-coverage-heading">
        <strong>{COVERAGE_AREA_LABEL[coverage.area]}</strong>
        <div className="engineering-analysis-structure-badges">
          <span className={`coverage-${coverage.status}`}>
            {COVERAGE_STATUS_LABEL[coverage.status]}
          </span>
          {coverage.truncated && <span className="coverage-truncated">일부만 수집</span>}
        </div>
      </div>
      {hasCount && (
        <p className="engineering-analysis-coverage-count">
          관측 {coverage.observedCount ?? "미상"}
          {coverage.totalCount !== undefined && ` / 전체 ${coverage.totalCount}`}
        </p>
      )}
      <DetailList label="제한 사항" values={coverage.limitations} />
      {coverage.evidenceRefs.length > 0 && (
        <details className="engineering-analysis-technical">
          <summary>연결된 실행 근거 {coverage.evidenceRefs.length}건</summary>
          <EvidenceRefs values={coverage.evidenceRefs} />
        </details>
      )}
    </li>
  );
}

function ProjectStructureCard({ fact }: { fact: EngineeringProjectStructureFact }) {
  return (
    <li className="engineering-analysis-coverage-card">
      <div className="engineering-analysis-coverage-heading">
        <div>
          <strong>{fact.name ?? PROJECT_STRUCTURE_KIND_LABEL[fact.kind]}</strong>
          <small>{PROJECT_STRUCTURE_KIND_LABEL[fact.kind]}</small>
        </div>
        <div className="engineering-analysis-structure-badges">
          <span className={`semantic-${fact.status}`}>
            {fact.status === "observed" ? "관측됨" : "부분 근거"}
          </span>
        </div>
      </div>
      {fact.attributes.length > 0 && (
        <dl className="engineering-analysis-technical">
          {fact.attributes.map((attribute) => (
            <div key={attribute.name}>
              <dt>{attribute.name}</dt>
              <dd><code>{String(attribute.value)}</code></dd>
            </div>
          ))}
        </dl>
      )}
      <details className="engineering-analysis-technical">
        <summary>기술 식별자</summary>
        <dl>
          <TechnicalValue label="evidenceRef" value={fact.evidenceRef} />
          <TechnicalValue label="entityId" value={fact.entityId} />
          <TechnicalValue label="parentId" value={fact.parentId} />
          <TechnicalValue label="order" value={fact.order} />
        </dl>
      </details>
    </li>
  );
}

export function EngineeringAnalysisPanel({
  analysis,
  digest,
  programs,
  focusAvailable,
  focusBusyProgramId,
  onFocusLocation,
}: EngineeringAnalysisPanelProps) {
  const locations = analysis.locations ?? [];
  const relations = analysis.relations ?? [];
  const coverage = analysis.coverage ?? [];
  const projectStructure = analysis.projectStructure ?? [];
  const uniquePrograms = buildUniqueProgramMap(programs);
  const classifiedCount =
    analysis.observations.length +
    analysis.inferences.length +
    analysis.hypotheses.length +
    analysis.alternatives.length +
    analysis.unknowns.length;
  const structuredCount =
    locations.length + relations.length + coverage.length + projectStructure.length;

  return (
    <section className="engineering-analysis" aria-label="구조화 엔지니어링 분석">
      <header className="engineering-analysis-header">
        <div>
          <span className="eyebrow">상세 분석</span>
          <h3>확인한 내용과 판단 근거</h3>
        </div>
        <div className="engineering-analysis-badges">
          {classifiedCount > 0 && <span>판단 {classifiedCount}</span>}
          {structuredCount > 0 && <span>구조 {structuredCount}</span>}
          <span>실행 근거 {analysis.evidenceRefs.length}</span>
          {analysis.unknowns.some((item) => item.blocking) && (
            <span className="analysis-blocking-badge">차단 미확인 있음</span>
          )}
        </div>
      </header>

      {locations.length > 0 && (
        <section className="engineering-analysis-structure">
          <div className="engineering-analysis-section-heading">
            <h4>근거 위치</h4>
            <span>{locations.length}곳</span>
          </div>
          <div className="engineering-analysis-location-list">
            {locations.map((location, index) => (
              <LocationCard
                key={`${location.evidenceRef}-${location.program}-${index}`}
                location={location}
                uniquePrograms={uniquePrograms}
                focusAvailable={focusAvailable}
                focusBusyProgramId={focusBusyProgramId}
                onFocusLocation={onFocusLocation}
              />
            ))}
          </div>
        </section>
      )}

      {relations.length > 0 && (
        <section className="engineering-analysis-structure">
          <div className="engineering-analysis-section-heading">
            <h4>신호 관계</h4>
            <span>{relations.length}건</span>
          </div>
          <ol className="engineering-analysis-relation-list">
            {relations.map((relation, index) => (
              <RelationCard
                key={`${relation.evidenceRef}-${relation.relationKind}-${index}`}
                relation={relation}
                uniquePrograms={uniquePrograms}
                focusAvailable={focusAvailable}
                focusBusyProgramId={focusBusyProgramId}
                onFocusLocation={onFocusLocation}
              />
            ))}
          </ol>
        </section>
      )}

      {coverage.length > 0 && (
        <section className="engineering-analysis-structure">
          <div className="engineering-analysis-section-heading">
            <h4>조사 범위</h4>
            <span>{coverage.length}개 영역</span>
          </div>
          <ul className="engineering-analysis-coverage-list">
            {coverage.map((item) => (
              <CoverageCard key={item.area} coverage={item} />
            ))}
          </ul>
        </section>
      )}

      {projectStructure.length > 0 && (
        <section className="engineering-analysis-structure">
          <div className="engineering-analysis-section-heading">
            <h4>프로젝트 구조 근거</h4>
            <span>{projectStructure.length}건</span>
          </div>
          <ul className="engineering-analysis-coverage-list">
            {projectStructure.map((fact) => (
              <ProjectStructureCard
                key={`${fact.evidenceRef}-${fact.entityId}`}
                fact={fact}
              />
            ))}
          </ul>
        </section>
      )}

      {analysis.observations.length > 0 && (
        <section className="engineering-analysis-group analysis-observations">
          <h4>관측 사실</h4>
          <ol>
            {analysis.observations.map((item, index) => (
              <li key={`observation-${index}-${item.statement}`}>
                {item.factKind && item.factFingerprint ? (
                  <>
                    <div className="engineering-analysis-item-heading">
                      <strong>Host fact · {item.factKind}</strong>
                      <code title={item.factFingerprint}>
                        {item.factFingerprint.slice(0, 12)}…
                      </code>
                    </div>
                    <code className="engineering-analysis-canonical-fact">
                      {item.statement}
                    </code>
                  </>
                ) : (
                  <p>{item.statement}</p>
                )}
                <EvidenceRefs values={item.evidenceRefs} />
              </li>
            ))}
          </ol>
        </section>
      )}

      {analysis.inferences.length > 0 && (
        <section className="engineering-analysis-group analysis-inferences">
          <h4>근거 있는 추론</h4>
          <ol>
            {analysis.inferences.map((item, index) => (
              <li key={`inference-${index}-${item.statement}`}>
                <div className="engineering-analysis-item-heading">
                  <p>{item.statement}</p>
                  <span className={`confidence confidence-${item.confidence}`}>
                    확신 {CONFIDENCE_LABEL[item.confidence]}
                  </span>
                </div>
                <EvidenceRefs values={item.evidenceRefs} />
              </li>
            ))}
          </ol>
        </section>
      )}

      {analysis.hypotheses.length > 0 && (
        <section className="engineering-analysis-group analysis-hypotheses">
          <h4>진단 가설</h4>
          <ol>
            {analysis.hypotheses.map((item, index) => {
              const v4Hypothesis = isV4Hypothesis(item);
              return (
                <li key={`hypothesis-${index}-${item.hypothesis}`}>
                <div className="engineering-analysis-item-heading">
                  <p>{item.hypothesis}</p>
                  <span className={`confidence confidence-${item.confidence}`}>
                    확신 {CONFIDENCE_LABEL[item.confidence]}
                  </span>
                  {v4Hypothesis ? (
                    <span
                      className={`hypothesis-verification hypothesis-verification-${item.verification}`}
                    >
                      {HYPOTHESIS_VERIFICATION_LABEL[item.verification]}
                    </span>
                  ) : (
                    <span className="hypothesis-verification hypothesis-verification-legacy">
                      검증 결과 미기록
                    </span>
                  )}
                </div>
                <EvidenceRefs values={item.supportingEvidenceRefs} />
                <DetailList label="반증·상충 근거" values={item.counterevidence} />
                <DetailList label="검증 계획" values={item.verificationSteps} />
                {v4Hypothesis ? (
                  <>
                    <DetailList
                      label="실제 검증 결과"
                      values={
                        item.verification === "open"
                          ? ["아직 검증 결과가 없습니다."]
                          : [item.outcome]
                      }
                    />
                    <div className="engineering-analysis-detail">
                      <strong>실제 검증 근거</strong>
                      <EvidenceRefs values={item.verificationEvidenceRefs} />
                    </div>
                  </>
                ) : (
                  <p className="engineering-analysis-verification">
                    실제 검증 결과와 근거는 이전 분석 버전에 기록되지 않았습니다.
                  </p>
                )}
                </li>
              );
            })}
          </ol>
        </section>
      )}

      {analysis.alternatives.length > 0 && (
        <section className="engineering-analysis-group analysis-alternatives">
          <h4>대안 비교</h4>
          <ol>
            {analysis.alternatives.map((item, index) => (
              <li key={`alternative-${index}-${item.option}`}>
                <p className="engineering-analysis-option">{item.option}</p>
                <DetailList label="예상 영향" values={item.expectedImpact} />
                <DetailList label="위험" values={item.risks} />
                <DetailList label="필요한 변경" values={item.requiredChanges ?? []} />
                <DetailList label="검증" values={item.verificationSteps} />
                <DetailList label="원복" values={item.rollback} />
                <EvidenceRefs values={item.evidenceRefs ?? []} />
              </li>
            ))}
          </ol>
        </section>
      )}

      {analysis.unknowns.length > 0 && (
        <section className="engineering-analysis-group analysis-unknowns">
          <h4>미확인 항목</h4>
          <ol>
            {analysis.unknowns.map((item, index) => (
              <li key={`unknown-${index}-${item.statement}`}>
                <div className="engineering-analysis-item-heading">
                  <p>{item.statement}</p>
                  <span className={item.blocking ? "unknown-blocking" : "unknown-open"}>
                    {item.blocking ? "다음 단계 차단" : "추가 확인"}
                  </span>
                </div>
                <p className="engineering-analysis-verification">
                  확인: {item.verificationStep}
                </p>
              </li>
            ))}
          </ol>
        </section>
      )}

      {analysis.nextActions.length > 0 && (
        <section className="engineering-analysis-next">
          <h4>다음 확인 순서</h4>
          <ol>
            {analysis.nextActions.map((item, index) => (
              <li key={`next-${index}-${item}`}>{item}</li>
            ))}
          </ol>
        </section>
      )}

      {classifiedCount === 0 && structuredCount === 0 && (
        <p className="engineering-analysis-empty">
          이 응답은 별도 진단 분류 없이 직접 답변만 제공합니다.
        </p>
      )}

      {digest && (
        <details className="engineering-analysis-technical">
          <summary>분석 기록 식별 정보</summary>
          <code>{digest}</code>
        </details>
      )}
    </section>
  );
}
