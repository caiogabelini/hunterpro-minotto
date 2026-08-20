import { useState, type FormEvent } from "react";

interface Props {
  razaoSocial: string;
  onConfirm: (motivo: string) => void;
  onCancel: () => void;
}

export function LossReasonModal({ razaoSocial, onConfirm, onCancel }: Props) {
  const [motivo, setMotivo] = useState("");

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!motivo.trim()) return;
    onConfirm(motivo.trim());
  }

  return (
    <div className="modal-backdrop" onClick={onCancel}>
      <form className="modal-card" onSubmit={handleSubmit} onClick={(e) => e.stopPropagation()}>
        <h2 className="modal-title">Motivo da perda</h2>
        <p className="modal-subtitle">{razaoSocial}</p>
        <textarea
          className="modal-textarea"
          value={motivo}
          onChange={(e) => setMotivo(e.target.value)}
          placeholder="Por que este lead foi perdido?"
          autoFocus
          required
        />
        <div className="modal-actions">
          <button type="button" className="modal-btn-secondary" onClick={onCancel}>
            Cancelar
          </button>
          <button type="submit" className="modal-btn-primary">
            Confirmar
          </button>
        </div>
      </form>
    </div>
  );
}
