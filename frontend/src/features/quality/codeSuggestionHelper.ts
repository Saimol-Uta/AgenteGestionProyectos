import type { FindingDetail } from "../../api/types";

export interface CodeSuggestion {
  code: string;
  language: "Python" | "TypeScript";
  explanation: string;
}

/** A deterministic display identity, not a cryptographic integrity guarantee. */
export function qualityDisplayFingerprint(value: string): string {
  let left = 2166136261;
  let right = 3339675911;
  for (let index = 0; index < value.length; index += 1) {
    left = Math.imul(left ^ value.charCodeAt(index), 16777619);
    right = Math.imul(right ^ value.charCodeAt(index), 2246822519);
  }
  return `${(left >>> 0).toString(16).padStart(8, "0")}${(right >>> 0).toString(16).padStart(8, "0")}`;
}

export function defaultQualityBranch(rule: string | null, findingId: string): string {
  const slug = (value: string) => value.toLowerCase().replace(/[^a-z0-9-]+/g, "-").replace(/^-+|-+$/g, "");
  return `ai-fix/calidad-${slug(rule ?? "general") || "general"}-${slug(findingId).slice(0, 20) || "hallazgo"}`;
}

export function validQualityBranch(value: string): boolean {
  return value.length > 0 && value.length <= 128 && value !== "@"
    && !/[\s~^:?*[\\\x00-\x1f\x7f]/.test(value)
    && !value.includes("..") && !value.includes("@{")
    && !value.startsWith("-")
    && value.split("/").every((part) => part.length > 0 && !part.startsWith(".") && !part.endsWith(".") && !part.endsWith(".lock"));
}

/** A code sample is shown only when it was returned by the backend. */
export function codeSuggestionFor(detail: FindingDetail): CodeSuggestion | null {
  const code = detail.suggested_code;
  const explanation = detail.improvement_summary;
  if (typeof code !== "string" || !code.trim() || typeof explanation !== "string" || !explanation.trim()) return null;
  const filePath = detail.file_path ?? detail.code_excerpt?.file_path ?? "";
  const language = /\.pyw?$/i.test(filePath) ? "Python" : "TypeScript";
  return { code, language, explanation };
}

/** Only a safe, backend-provided GitHub branch URL is rendered as an external link. */
export function safeGitHubBranchUrl(value: string | null | undefined): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    if (url.protocol !== "https:" || url.hostname !== "github.com" || url.port || url.username || url.password) return null;
    return /^\/[^/]+\/[^/]+\/tree\/.+\/?$/.test(url.pathname) ? url.href : null;
  } catch { return null; }
}

/**
 * Only a real pull request address is rendered as an external link.
 *
 * A simulated publication carries a `sandbox://` address, so it can never be
 * opened in a browser and can never be read as a real pull request.
 */
export function safeGitHubPullRequestUrl(value: string | null | undefined): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    if (url.protocol !== "https:" || url.hostname !== "github.com" || url.port || url.username || url.password) return null;
    return /^\/[^/]+\/[^/]+\/pull\/\d+\/?$/.test(url.pathname) ? url.href : null;
  } catch { return null; }
}

/** Whether a publication receipt describes a simulated pull request. */
export function isSimulatedPublication(state: string | null | undefined): boolean {
  return state === "simulated";
}
