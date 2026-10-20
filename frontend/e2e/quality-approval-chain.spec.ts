import { expect, test } from "@playwright/test";
import {
  approvalEligibility,
  assertNoBrowserNetworkAttempts,
  blockedApprovalEligibility,
  findingDetail,
  installApi,
  installBrowserNetworkGuard,
  patchReview,
} from "./fixtures";

/**
 * The quality centre separates four facts: the exact patch review, the
 * eligibility answered by the backend, the human decision and the
 * publication. The approval is enabled only when the backend says the action
 * is eligible and the manager session allows it, and a simulated pull
 * request never appears as a real one.
 */

const detailWithChain = {
  ...findingDetail,
  suggested_code: "const token = readToken().trim();",
  improvement_summary: "Normaliza el valor de entrada antes de usarlo.",
  decision_status: "pending",
  patch_review: patchReview,
  approval_eligibility: approvalEligibility,
  publication: null,
};

test.beforeEach(async ({ page }) => {
  await installBrowserNetworkGuard(page);
});

test.afterEach(async ({ page }) => {
  assertNoBrowserNetworkAttempts(page);
});

async function openFinding(page: import("@playwright/test").Page) {
  await page.goto("/quality");
  await page.getByRole("button", { name: "Revisar hallazgo finding-candidate" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
}

test("muestra la cadena completa y habilita la aprobación solo cuando el backend la autoriza", async ({ page }) => {
  const api = await installApi(page, {
    authenticated: true,
    capabilities: ["quality:decide"],
    findingDetailPayload: detailWithChain,
  });

  await openFinding(page);

  // 1. The exact patch review is shown with the identity it covers.
  const review = page.getByRole("region", { name: "Revisión del parche exacto" });
  await expect(review).toBeVisible();
  await expect(review.getByText(patchReview.review_id)).toBeVisible();
  await expect(review.getByText(patchReview.patch_sha256)).toBeVisible();
  await expect(review.getByText("Veredicto: Favorable")).toBeVisible();

  // 2. The eligibility is the backend answer, shown as such.
  const eligibility = page.getByRole("region", { name: "Elegibilidad de la aprobación" });
  await expect(eligibility).toBeVisible();
  await expect(eligibility.getByText("El backend autoriza la aprobación")).toBeVisible();
  await expect(eligibility.getByText("Defecto confirmado")).toBeVisible();

  // 3. The approval is enabled for a manager session with an eligible action.
  const approve = page.getByRole("button", { name: "Aprobar y crear rama" });
  await expect(approve).toBeEnabled();

  // 4. The approval records the decision and shows branch, commit and pull request.
  await approve.click();
  await expect(page.getByText("quality/finding-candidate-g4", { exact: true })).toBeVisible();
  await expect(page.getByText(`Commit informado: ${"d".repeat(64)}`)).toBeVisible();
  const publication = page.getByRole("region", { name: "Publicación del pull request" });
  await expect(publication.getByText("Pull request simulado")).toBeVisible();
  await expect(publication.getByText("Pull request #4321")).toBeVisible();
  await expect(publication.getByText("sandbox://pulls/owner/project/4321")).toBeVisible();
  await expect(publication.getByText(/es una simulación de sandbox/)).toBeVisible();
  // A simulated pull request is never a clickable address.
  await expect(publication.locator("a")).toHaveCount(0);

  expect(api.findingDecisionRequests).toHaveLength(1);
  expect(api.findingDecisionRequests[0].idempotencyKey).toBeTruthy();
  expect(api.findingDecisionRequests[0].csrfToken).toBe("csrf-fixture");
});

test("bloquea la aprobación y explica el motivo cuando el backend no la autoriza", async ({ page }) => {
  await installApi(page, {
    authenticated: true,
    capabilities: ["quality:decide"],
    findingDetailPayload: {
      ...detailWithChain,
      patch_review: null,
      approval_eligibility: {
        ...blockedApprovalEligibility,
        blocked_reason: "revision_inexistente",
        blocked_reason_message:
          "No existe una revisión independiente del parche exacto que se aplicaría.",
      },
    },
  });

  await openFinding(page);

  const eligibility = page.getByRole("region", { name: "Elegibilidad de la aprobación" });
  await expect(eligibility.getByText("El backend no autoriza la aprobación")).toBeVisible();
  await expect(eligibility.getByText("revision_inexistente")).toBeVisible();
  await expect(page.getByRole("button", { name: "Aprobar y crear rama" })).toBeDisabled();
});

test("una edición del código vuelve a consultar al backend y no extiende la revisión mostrada", async ({ page }) => {
  const api = await installApi(page, {
    authenticated: true,
    capabilities: ["quality:decide"],
    findingDetailPayload: detailWithChain,
    approvalEligibilityPayload: blockedApprovalEligibility,
  });

  await openFinding(page);
  await expect(page.getByRole("button", { name: "Aprobar y crear rama" })).toBeEnabled();

  await page.getByRole("button", { name: "Editar el código sugerido" }).click();
  const editor = page.getByLabel("Corrección editada por la gestora");
  await editor.fill("const token = readToken().trim().toUpperCase();");

  // The backend is asked about the edited patch, and its answer is the one shown.
  await expect.poll(() => api.findingApprovalRequests.length).toBe(1);
  const request = JSON.parse(api.findingApprovalRequests[0].body ?? "{}");
  expect(request.suggested_code_override).toBe("const token = readToken().trim().toUpperCase();");
  await expect(page.getByRole("button", { name: "Aprobar y crear rama" })).toBeDisabled();
  await expect(page.getByText(/el código editado es otro parche y no tiene revisión vigente/)).toBeVisible();
  await expect(page.getByText("revision_no_corresponde")).toBeVisible();
});

test("una publicación parcial ofrece reintento con la misma clave de idempotencia", async ({ page }) => {
  const api = await installApi(page, {
    authenticated: true,
    capabilities: ["quality:decide"],
    findingDetailPayload: detailWithChain,
    decisionPayload: {
      reused: false,
      audit_event_id: "audit-finding-g4-0002",
      decision_status: "approved",
      branch_name: "quality/finding-candidate-g4",
      branch_url: "sandbox://branches/quality/finding-candidate-g4",
      commit_sha_applied: "d".repeat(64),
      suggested_code: "const token = readToken().trim();",
      improvement_summary: "Normaliza el valor de entrada antes de usarlo.",
      execution_mode: "sandbox",
      publication_status: "partial",
      publication_error_code: "pull_request_no_confirmado",
      publication_error_message: "No se pudo confirmar la creación del pull request.",
      pull_request_number: null,
      pull_request_url: null,
      pull_request_state: null,
      pull_request_simulated: null,
      patch_review_id: patchReview.review_id,
      patch_sha256: patchReview.patch_sha256,
    },
  });

  await openFinding(page);
  await page.getByRole("button", { name: "Aprobar y crear rama" }).click();

  const publication = page.getByRole("region", { name: "Publicación del pull request" });
  await expect(publication.getByText("Publicación parcial")).toBeVisible();
  await expect(publication.getByText("pull_request_no_confirmado")).toBeVisible();
  await expect(publication.getByText(/La rama y el commit se conservaron/)).toBeVisible();

  const retry = publication.getByRole("button", { name: "Reintentar la publicación" });
  await expect(retry).toBeEnabled();
  await retry.click();

  await expect.poll(() => api.findingDecisionRequests.length).toBe(2);
  expect(api.findingDecisionRequests[1].idempotencyKey)
    .toBe(api.findingDecisionRequests[0].idempotencyKey);
});
