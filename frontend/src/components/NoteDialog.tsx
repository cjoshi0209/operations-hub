import { FormEvent, useState } from "react";
import Modal from "./Modal";

export default function NoteDialog({
  title,
  label,
  required,
  confirmLabel,
  pending,
  onConfirm,
  onCancel,
}: {
  title: string;
  label: string;
  required: boolean;
  confirmLabel: string;
  pending: boolean;
  onConfirm: (note: string) => void;
  onCancel: () => void;
}) {
  const [note, setNote] = useState("");
  function submit(e: FormEvent) {
    e.preventDefault();
    onConfirm(note.trim());
  }
  return (
    <Modal title={title} onClose={onCancel} busy={pending}>
      <form onSubmit={submit}>
        <label>
          {label}
          <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={3} required={required} />
        </label>
        <div className="actions">
          <button type="button" onClick={onCancel} disabled={pending}>
            Cancel
          </button>
          <button className="primary" disabled={pending || (required && !note.trim())}>
            {pending ? "Working…" : confirmLabel}
          </button>
        </div>
      </form>
    </Modal>
  );
}
