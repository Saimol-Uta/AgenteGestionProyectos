import * as Dialog from "@radix-ui/react-dialog";
import { ShieldAlert, X } from "lucide-react";

export function ConfirmActionDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel,
  onConfirm,
  working = false,
  danger = false,
  error,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description: string;
  confirmLabel: string;
  onConfirm: () => void;
  working?: boolean;
  danger?: boolean;
  error?: string;
}) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="dialog-overlay" />
        <Dialog.Content className="dialog-content confirm-action-dialog">
          <div className="dialog-heading">
            <span className={`dialog-icon${danger ? " confirm-dialog-icon-danger" : ""}`}><ShieldAlert aria-hidden="true" size={18} /></span>
            <div>
              <Dialog.Title className="dialog-title">{title}</Dialog.Title>
              <Dialog.Description className="dialog-description">{description}</Dialog.Description>
            </div>
            <Dialog.Close asChild>
              <button type="button" className="icon-button dialog-close" aria-label="Cerrar confirmación" disabled={working}>
                <X aria-hidden="true" size={16} />
              </button>
            </Dialog.Close>
          </div>
          {error && <p role="alert" className="inline-warning">{error}</p>}
          <div className="dialog-actions">
            <Dialog.Close asChild>
              <button type="button" className="button button-secondary" disabled={working}>Cancelar</button>
            </Dialog.Close>
            <button
              type="button"
              className={`button ${danger ? "button-danger" : "button-primary"}`}
              onClick={onConfirm}
              disabled={working}
            >
              {working ? "Procesando…" : confirmLabel}
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
