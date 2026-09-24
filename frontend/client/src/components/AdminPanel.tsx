import { useEffect, useState } from "react";
import { BookOpen, Dumbbell, KeyRound, Smartphone, X } from "lucide-react";
import { api, ApiError, type AdminRow } from "@/lib/api";

const isoDate = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

/** 관리자(홍승원) 전용: 날짜를 골라 대신 인증하거나 취소하고, PIN·기기 등록을 초기화한다. */
export default function AdminPanel({ onClose, notify }: { onClose: () => void; notify: (message: string) => void }) {
  const today = isoDate(new Date());
  const [day, setDay] = useState(today);
  const [rows, setRows] = useState<AdminRow[]>([]);
  const [busy, setBusy] = useState("");

  const fail = (error: unknown) => notify(error instanceof ApiError ? error.message : "서버에 연결하지 못했어요.");

  useEffect(() => {
    api.adminDay(day).then((data) => setRows(data.members)).catch(fail);
  }, [day]);

  const toggle = async (member: AdminRow, type: "qt" | "exercise") => {
    const done = !(type === "qt" ? member.qt : member.exercise);
    setBusy(member.id + type);
    try {
      setRows((await api.adminSetActivity(member.id, day, type, done)).members);
      notify(`${member.name}님 ${type === "qt" ? "큐티" : "운동"} ${done ? "인증 처리했어요" : "취소했어요"}.`);
    } catch (error) {
      fail(error);
    } finally {
      setBusy("");
    }
  };

  const resetPin = async (member: AdminRow) => {
    if (!window.confirm(`${member.name}님의 PIN을 공통 PIN으로 되돌릴까요?`)) return;
    try {
      const { pin } = await api.adminResetPin(member.id);
      setRows((prev) => prev.map((r) => (r.id === member.id ? { ...r, default_pin: true } : r)));
      notify(`${member.name}님 PIN을 ${pin}로 되돌렸어요.`);
    } catch (error) {
      fail(error);
    }
  };

  const resetPasskeys = async (member: AdminRow) => {
    if (!window.confirm(`${member.name}님의 등록 기기를 모두 해제할까요?`)) return;
    try {
      await api.adminResetPasskeys(member.id);
      setRows((prev) => prev.map((r) => (r.id === member.id ? { ...r, devices: 0 } : r)));
      notify(`${member.name}님 기기 등록을 해제했어요.`);
    } catch (error) {
      fail(error);
    }
  };

  const cell = { display: "flex", alignItems: "center", gap: 6, fontSize: 12 } as const;
  const chip = (on: boolean) => ({
    display: "flex", alignItems: "center", gap: 4, padding: "6px 10px", borderRadius: 10, fontSize: 12,
    border: `1px solid ${on ? "#2b9f90" : "rgba(0,0,0,.12)"}`, background: on ? "#2b9f90" : "transparent",
    color: on ? "#fff" : "inherit", cursor: "pointer",
  } as const);

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="member-modal" style={{ maxWidth: 520, width: "94vw" }} onClick={(event) => event.stopPropagation()}>
        <button className="modal-close" aria-label="닫기" onClick={onClose}><X size={18} /></button>
        <span className="section-kicker">ADMIN · 인증 관리</span>
        <h2 style={{ marginBottom: 12 }}>대신 인증하기</h2>

        <label style={{ ...cell, justifyContent: "center", marginBottom: 14 }}>
          날짜
          <input type="date" value={day} max={today} onChange={(event) => setDay(event.target.value)}
                 style={{ padding: "8px 10px", borderRadius: 10, border: "1px solid rgba(0,0,0,.15)", fontSize: 14 }} />
        </label>

        <div style={{ display: "grid", gap: 8, maxHeight: "52vh", overflowY: "auto", textAlign: "left" }}>
          {rows.map((member) => (
            <div key={member.id} style={{ display: "grid", gridTemplateColumns: "1fr auto auto", gap: 8, alignItems: "center",
                                          padding: "8px 10px", borderRadius: 12, background: "rgba(0,0,0,.03)" }}>
              <div>
                <b style={{ fontSize: 14 }}>{member.name}</b>
                <div style={{ fontSize: 11, opacity: 0.65 }}>
                  {member.total_distance}m · 연속 {member.streak}일 · 기기 {member.devices}
                  {member.default_pin && " · PIN 미변경"}
                </div>
              </div>
              <button style={chip(member.qt)} disabled={busy === member.id + "qt"} onClick={() => toggle(member, "qt")}>
                <BookOpen size={13} /> 큐티
              </button>
              <button style={chip(member.exercise)} disabled={busy === member.id + "exercise"} onClick={() => toggle(member, "exercise")}>
                <Dumbbell size={13} /> 운동
              </button>
              <div style={{ gridColumn: "1 / -1", display: "flex", gap: 8 }}>
                <button className="text-button" style={{ ...cell, fontSize: 11 }} onClick={() => resetPin(member)}>
                  <KeyRound size={12} /> PIN 초기화
                </button>
                <button className="text-button" style={{ ...cell, fontSize: 11 }} onClick={() => resetPasskeys(member)}>
                  <Smartphone size={12} /> 기기 해제
                </button>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
