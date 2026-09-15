import { readFileSync } from "node:fs";

const nativeSource = readFileSync(
  new URL("../src/components/NativeProjectPanel.tsx", import.meta.url),
  "utf8",
);
const reviewSource = readFileSync(
  new URL("../src/components/NewProjectPlanReviewPanel.tsx", import.meta.url),
  "utf8",
);

function requireText(condition, message) {
  if (!condition) throw new Error(message);
}

const userProof = nativeSource.indexOf('data-testid="native-project-user-proof"');
const technical = nativeSource.indexOf('data-testid="native-project-technical-details"');
requireText(userProof >= 0 && userProof < technical, "human project proof must precede technical details");
requireText(
  nativeSource.slice(userProof, technical).includes("Program Check") &&
    nativeSource.slice(userProof, technical).includes("독립 재열기") &&
    nativeSource.slice(userProof, technical).includes("XG-SIM 시나리오") &&
    nativeSource.slice(userProof, technical).includes("PLC 영향"),
  "default project proof must retain human-readable verification state",
);
const nativeTechnicalBody = nativeSource.slice(technical);
for (const marker of ["작업 식별자", "SHA-256", "receiptDigest"]) {
  requireText(nativeTechnicalBody.includes(marker), `${marker} must remain available in technical details`);
}
const reviewTechnical = reviewSource.indexOf('data-testid="project-plan-technical-details"');
const reviewTechnicalBody = reviewSource.slice(
  reviewTechnical,
  reviewSource.indexOf("</details>", reviewTechnical),
);
requireText(
  reviewTechnical >= 0 &&
    reviewTechnicalBody.includes("<summary>기술 세부 정보</summary>") &&
    reviewTechnicalBody.includes("<code>{planDigest}</code>") &&
    reviewTechnicalBody.includes("<code>{currentReview.actorId}</code>"),
  "plan digests must remain available in closed technical details",
);

console.log("technical disclosure contract verified");
