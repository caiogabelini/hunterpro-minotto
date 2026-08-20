import { DndContext, PointerSensor, useSensor, useSensors, type DragEndEvent } from "@dnd-kit/core";
import { useEffect, useState } from "react";
import { fetchLeads, updateLeadStatus, UnauthorizedError, type Lead } from "../api";
import { KanbanColumn } from "../components/KanbanColumn";
import { LossReasonModal } from "../components/LossReasonModal";
import { useAuth } from "../context/AuthContext";
import { KANBAN_COLUMNS, STATUS_PERDIDO } from "../kanbanStatuses";
import "./KanbanPage.css";

export function KanbanPage() {
  const { token, logout } = useAuth();
  const [leads, setLeads] = useState<Lead[]>([]);
  const [carregando, setCarregando] = useState(true);
  const [erro, setErro] = useState<string | null>(null);
  const [pendingLoss, setPendingLoss] = useState<{ leadId: string; razaoSocial: string } | null>(null);

  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 6 } }));

  useEffect(() => {
    if (!token) return;
    fetchLeads(token)
      .then(setLeads)
      .catch((e) => {
        if (e instanceof UnauthorizedError) {
          logout();
          return;
        }
        setErro(String(e));
      })
      .finally(() => setCarregando(false));
  }, [token, logout]);

  async function aplicarNovoStatus(leadId: string, novoStatus: string, motivoPerda?: string) {
    if (!token) return;
    const anteriores = leads;
    setLeads((atuais) =>
      atuais.map((l) => (l.id === leadId ? { ...l, kanban_status: novoStatus, motivo_perda: motivoPerda ?? null } : l)),
    );
    try {
      await updateLeadStatus(token, leadId, novoStatus, motivoPerda);
    } catch (e) {
      setLeads(anteriores);
      if (e instanceof UnauthorizedError) {
        logout();
        return;
      }
      setErro(String(e));
    }
  }

  function handleDragEnd(event: DragEndEvent) {
    const { active, over } = event;
    if (!over) return;

    const leadId = String(active.id);
    const novoStatus = String(over.id);
    const lead = leads.find((l) => l.id === leadId);
    if (!lead || lead.kanban_status === novoStatus) return;

    if (novoStatus === STATUS_PERDIDO) {
      setPendingLoss({ leadId, razaoSocial: lead.razao_social });
      return;
    }
    void aplicarNovoStatus(leadId, novoStatus);
  }

  const leadsPorStatus = new Map<string, Lead[]>(KANBAN_COLUMNS.map((c) => [c.status, []]));
  for (const lead of leads) {
    leadsPorStatus.get(lead.kanban_status)?.push(lead);
  }

  return (
    <div className="kanban-page">
      <header className="kanban-header">
        <div>
          <div className="kanban-eyebrow">HunterPro</div>
          <h1 className="kanban-title">Minotto Contabilidade</h1>
        </div>
        <button className="kanban-logout" onClick={logout}>
          Sair
        </button>
      </header>

      {erro && <p className="kanban-erro">{erro}</p>}
      {carregando && <p className="kanban-carregando">Carregando leads...</p>}

      {!carregando && (
        <DndContext sensors={sensors} onDragEnd={handleDragEnd}>
          <div className="kanban-board">
            {KANBAN_COLUMNS.map(({ status, label }) => (
              <KanbanColumn key={status} status={status} label={label} leads={leadsPorStatus.get(status) ?? []} />
            ))}
          </div>
        </DndContext>
      )}

      {pendingLoss && (
        <LossReasonModal
          razaoSocial={pendingLoss.razaoSocial}
          onCancel={() => setPendingLoss(null)}
          onConfirm={(motivo) => {
            void aplicarNovoStatus(pendingLoss.leadId, STATUS_PERDIDO, motivo);
            setPendingLoss(null);
          }}
        />
      )}
    </div>
  );
}
