import { useDemoState } from "../demo/useDemoState";

export function isDeliveryRecipient(value: unknown): value is string {
  return typeof value === "string" && (value === "" || (
    value.length <= 254 && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value)
  ));
}

/** A browser preference only; it does not configure a server or grant delivery authority. */
export function useDeliveryPreferences(projectId: string | null): { recipient: string; setRecipient: (value: string) => void } {
  const [recipient, saveRecipient] = useDemoState(`delivery-recipient:${projectId ?? "unselected"}`, "", isDeliveryRecipient);
  return {
    recipient: projectId ? recipient : "",
    setRecipient: (value) => { if (projectId) saveRecipient(value.trim()); },
  };
}
