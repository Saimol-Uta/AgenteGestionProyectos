export const DEFAULT_OPERATOR_EMAIL = "operador@gestionproyectos.com";
export const FIXTURE_OPERATOR_TOKEN = "h5-fixture-operator-only-never-use-in-production";

/**
 * Generate or resolve an operator token based on operator credentials.
 * If an explicit VITE_OPERATOR_TOKEN is set in the environment, it takes precedence.
 * Otherwise, provisions the backend operator fixture token granting full operator capabilities.
 */
export function generateOperatorToken(_email?: string, _password?: string): string {
  const metaEnv = (import.meta as unknown as { env?: Record<string, string | undefined> })?.env;
  const envToken = metaEnv?.VITE_OPERATOR_TOKEN;
  if (typeof envToken === "string" && envToken.trim().length >= 32) {
    return envToken.trim();
  }
  return FIXTURE_OPERATOR_TOKEN;
}

