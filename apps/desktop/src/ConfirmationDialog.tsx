import type { ReactNode } from "react";

import { ModalDialog } from "./ModalDialog";

export function ConfirmationDialog({
  open,
  title,
  description,
  confirmLabel,
  busy = false,
  destructive = false,
  details,
  onClose,
  onConfirm,
}: {
  open: boolean;
  title: string;
  description: string;
  confirmLabel: string;
  busy?: boolean;
  destructive?: boolean;
  details?: ReactNode;
  onClose: () => void;
  onConfirm: () => void;
}) {
  return <ModalDialog open={open} title={title} description={description} onClose={onClose}>
    <div className="confirmation-dialog-content">
      {details && <div className="confirmation-details">{details}</div>}
      <div className="modal-sticky-actions">
        <button className="secondary-button" type="button" disabled={busy} onClick={onClose}>返回检查</button>
        <button className={destructive ? "danger-button" : "primary-button"} type="button" disabled={busy} onClick={onConfirm}>
          {busy ? "正在处理…" : confirmLabel}
        </button>
      </div>
    </div>
  </ModalDialog>;
}
