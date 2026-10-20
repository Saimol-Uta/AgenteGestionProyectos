import { expect, test, type BrowserContext, type Page, type Request } from "@playwright/test";

/**
 * The integrated G4 proof: React -> FastAPI -> SQLite -> GitHub sandbox.
 *
 * No API is mocked. The finding, the confirmed defect, the exact patch and
 * the independent favorable review are real persisted records; the branch and
 * the commit are real files in the sandbox root; the pull request is
 * explicitly simulated and is never presented as a real one.
 */

const frontendOrigin = "http://127.0.0.1:4173";
const apiRoot = `${frontendOrigin}/api/v1`;
const projectId = "G4-SANDBOX";
const runId = "run-g4-sandbox";
const findingId = "backend:f-g4-sandbox";
const operatorToken = "g4-sandbox-operator-token-never-use-in-production";

function allowedOrigin(value: string): boolean {
  const url = new URL(value);
  return url.protocol === "http:" && url.hostname === "127.0.0.1" && url.port === "4173";
}

async function installNetworkGuard(page: Page): Promise<string[]> {
  const unexpected: string[] = [];
  page.on("request", (request: Request) => {
    if (!allowedOrigin(request.url())) {
      unexpected.push(request.url());
    }
  });
  return unexpected;
}

async function login(context: BrowserContext) {
  const response = await context.request.post(`${apiRoot}/auth/login`, {
    data: { token: operatorToken },
    headers: { Origin: frontendOrigin },
  });
  expect(response.status()).toBe(200);
  const session = await response.json() as { authenticated: boolean; csrf_token: string };
  expect(session.authenticated).toBe(true);
  expect(session.csrf_token).toBeTruthy();
  return session;
}

test("cadena real: hallazgo detectado, defecto confirmado, parche revisado, aprobacion, rama, commit y pull request simulado", async ({ page, context }) => {
  const unexpected = await installNetworkGuard(page);
  const session = await login(context);

  // 1. The backend really exposes the exact patch review and the eligibility.
  const detailResponse = await context.request.get(`${apiRoot}/runs/${runId}/findings/${encodeURIComponent(findingId)}`);
  expect(detailResponse.status()).toBe(200);
  const detail = await detailResponse.json() as {
    finding_id: string;
    verification_status: string;
    suggested_code: string;
    patch_review: { review_id: string; patch_sha256: string; verdict: string } | null;
    approval_eligibility: {
      eligible: boolean;
      defect_confirmed: boolean;
      blocked_reason: string | null;
      patch_sha256: string | null;
    } | null;
  };
  expect(detail.finding_id).toBe(findingId);
  expect(detail.verification_status).toBe("confirmed");
  expect(detail.suggested_code).toBe("    return value.strip()");
  expect(detail.patch_review).toBeTruthy();
  expect(detail.patch_review?.verdict).toBe("favorable");
  expect(detail.approval_eligibility?.eligible).toBe(true);
  expect(detail.approval_eligibility?.defect_confirmed).toBe(true);
  expect(detail.approval_eligibility?.blocked_reason).toBeNull();
  expect(detail.approval_eligibility?.patch_sha256).toBe(detail.patch_review?.patch_sha256);

  // 2. The interface shows the four facts and enables the approval.
  await page.goto("/quality");
  await page.getByRole("button", { name: `Revisar hallazgo ${findingId}` }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  const review = dialog.getByRole("region", { name: "Revisión del parche exacto" });
  await expect(review).toContainText(detail.patch_review!.review_id);
  await expect(review).toContainText(detail.patch_review!.patch_sha256);
  const eligibility = dialog.getByRole("region", { name: "Elegibilidad de la aprobación" });
  await expect(eligibility).toContainText("El backend autoriza la aprobación");
  await expect(eligibility).toContainText("Defecto confirmado");

  const approve = dialog.getByRole("button", { name: "Aprobar y crear rama" });
  await expect(approve).toBeEnabled();

  // 3. The human approval creates the branch and the commit in the sandbox.
  await approve.click();
  const result = dialog.locator(".quality-decision-result");
  await expect(result.getByText("Aprobada", { exact: true })).toBeVisible();
  await expect(result.locator("strong")).toContainText("quality/");

  const decisions = await context.request.get(`${apiRoot}/runs/${runId}/findings/${encodeURIComponent(findingId)}`);
  expect(decisions.status()).toBe(200);
  const decided = await decisions.json() as {
    decision_status: string;
    branch_name: string | null;
    commit_sha_applied: string | null;
    publication: {
      publication_status: string;
      pull_request_number: number | null;
      pull_request_url: string | null;
      pull_request_state: string | null;
      pull_request_simulated: boolean | null;
      patch_review_id: string | null;
      patch_sha256: string | null;
    } | null;
  };
  expect(decided.decision_status).toBe("approved");
  expect(decided.branch_name).toBeTruthy();
  expect(decided.commit_sha_applied).toBeTruthy();

  // 4. The publication receipt is positive: one published, explicitly
  //    simulated pull request of the reviewed patch, never a real one.
  const publication = dialog.getByRole("region", { name: "Publicación del pull request" });
  await expect(publication).toBeVisible();
  const publicationText = await publication.innerText();
  expect(publicationText).not.toContain("no registró ninguna publicación");
  expect(publicationText).not.toContain("Publicación parcial");
  await expect(publication.getByText("Pull request simulado")).toBeVisible();
  await expect(publicationText).toContain("sandbox://pulls/");
  // A simulated pull request is never a clickable address.
  await expect(publication.locator("a")).toHaveCount(0);

  // 5. The durable receipt of the backend agrees with the interface.
  expect(decided.publication).toBeTruthy();
  expect(decided.publication!.publication_status).toBe("published");
  expect(decided.publication!.pull_request_state).toBe("simulated");
  expect(decided.publication!.pull_request_simulated).toBe(true);
  expect(decided.publication!.pull_request_number).toBeGreaterThan(0);
  expect(decided.publication!.pull_request_url).toMatch(/^sandbox:\/\/pulls\//);
  expect(decided.publication!.pull_request_url).toContain(
    String(decided.publication!.pull_request_number),
  );
  expect(decided.publication!.patch_review_id).toBe(detail.patch_review?.review_id);
  expect(decided.publication!.patch_sha256).toBe(detail.patch_review?.patch_sha256);

  expect(unexpected).toEqual([]);
});
