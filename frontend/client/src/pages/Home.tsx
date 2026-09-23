import { useEffect, useMemo, useState, type CSSProperties } from "react";
import { api, ApiError, fetchLoginOptions, fetchRegisterOptions, getToken, isInAppBrowser, loginPasskey, passkeyErrorMessage, passkeySupported, registerPasskey, setToken, type ApiUser, type BoardRow, type CalendarDay, type Poke, type PasskeyRequest } from "@/lib/api";
import {
  ArrowRight,
  Bell,
  BookOpen,
  CalendarDays,
  Check,
  ChevronRight,
  Dumbbell,
  Flame,
  Heart,
  LockKeyhole,
  Medal,
  MessageCircle,
  MoreHorizontal,
  Play,
  RotateCcw,
  Send,
  ShieldCheck,
  Sparkles,
  Trophy,
  UserRound,
  Users,
  X,
  Zap,
} from "lucide-react";
import { toast as sonnerToast } from "sonner";

type HabitKind = "devotional" | "exercise";
type ActiveTab = "race" | "routine" | "record";
type AuthStage = "name" | "pin";

type Member = {
  id: string;
  name: string;
  initials: string;
  shell: string;
  accent: string;
  order: number;
  rank: number;
  streak: number;
  distance: number;
  today: Record<HabitKind, boolean>;
  feeling: string;
  cheerCount: number;
};

const GUIDE_IMAGE_URL = "/manus-storage/guide-otter_fa06dbfa.png";
const SEEN_POKE_KEY = "turtleQuestSeenPoke";

// 멤버 순서(sort_order)별 거북이 등껍질/포인트 색
const PALETTE: [string, string][] = [
  ["#71d7c3", "#2b9f90"], ["#ff9a8c", "#e56455"], ["#f8d26a", "#d8982b"], ["#b7a3f5", "#7258c8"],
  ["#ffbd76", "#e78837"], ["#84c7ef", "#348aca"], ["#f09bbe", "#c84d80"], ["#a8d56c", "#6da338"],
  ["#62d2dc", "#2895a1"], ["#f6a35e", "#cf6825"], ["#d3a8ec", "#8b5ab9"], ["#7ba5f4", "#476ec2"],
  ["#e7dd75", "#b1a628"], ["#c68ac8", "#864484"],
];
const colorsFor = (order: number) => PALETTE[Math.abs(order - 1) % PALETTE.length];

const toMember = (row: BoardRow): Member => {
  const [shell, accent] = colorsFor(row.sort_order);
  return {
    id: row.id, name: row.name, initials: row.name.slice(1), shell, accent, order: row.sort_order, rank: row.rank,
    streak: row.streak, distance: row.total_distance, today: { devotional: row.today.qt, exercise: row.today.exercise },
    feeling: row.memo, cheerCount: row.cheers,
  };
};

const EMPTY_MEMBER: Member = {
  id: "", name: "", initials: "", shell: PALETTE[0][0], accent: PALETTE[0][1], order: 0, rank: 0,
  streak: 0, distance: 0, today: { devotional: false, exercise: false }, feeling: "", cheerCount: 0,
};

const isoDate = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

function getDateLabel() {
  return new Intl.DateTimeFormat("ko-KR", { month: "long", day: "numeric", weekday: "short" }).format(new Date());
}

function Turtle({ member, moving = false, compact = false }: { member: Member; moving?: boolean; compact?: boolean }) {
  return (
    <div className={`turtle-wrap ${moving ? "is-moving" : ""} ${compact ? "is-compact" : ""}`} aria-label={`${member.name}의 거북이`}>
      <div className="turtle-dust" aria-hidden="true"><i /><i /><i /></div>
      <div
        className="turtle-pixel"
        style={{ "--turtle-shell": member.shell, "--turtle-accent": member.accent } as CSSProperties}
      >
        <span className="turtle-head" />
        <span className="turtle-shell" />
        <span className="turtle-leg leg-a" />
        <span className="turtle-leg leg-b" />
        <span className="turtle-eye" />
        {member.streak >= 10 && <span className="streak-crown">✦</span>}
      </div>
    </div>
  );
}

function PixelIcon({ kind, done }: { kind: HabitKind; done: boolean }) {
  return (
    <span className={`pixel-icon ${kind} ${done ? "done" : ""}`} aria-hidden="true">
      {kind === "devotional" ? <BookOpen size={16} strokeWidth={2.5} /> : <Dumbbell size={16} strokeWidth={2.5} />}
    </span>
  );
}

function StatPill({ icon, label, value, tone }: { icon: React.ReactNode; label: string; value: string; tone: string }) {
  return (
    <div className={`stat-pill ${tone}`}>
      <span className="stat-icon">{icon}</span>
      <span><b>{value}</b><small>{label}</small></span>
    </div>
  );
}

export default function Home() {
  const [members, setMembers] = useState<Member[]>([]);
  const [roster, setRoster] = useState<ApiUser[]>([]);
  const [pokes, setPokes] = useState<Poke[]>([]);
  const [currentUserId, setCurrentUserId] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<ActiveTab>("race");
  const [selectedMemberId, setSelectedMemberId] = useState<string | null>(null);
  const [showOnboarding, setShowOnboarding] = useState(!getToken());
  const [authStage, setAuthStage] = useState<AuthStage>("name");
  const [authName, setAuthName] = useState(() => localStorage.getItem("turtleQuestUser") ?? "");
  const [pin, setPin] = useState("");
  const [pinError, setPinError] = useState("");
  const [webauthnPending, setWebauthnPending] = useState(false);
  const [pinChange, setPinChange] = useState<null | { forced: boolean }>(null);
  const [passkeyAfterPinChange, setPasskeyAfterPinChange] = useState(false);
  const [registerReq, setRegisterReq] = useState<PasskeyRequest | null>(null);
  const [loginReq, setLoginReq] = useState<PasskeyRequest | null>(null);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState("");
  const [noteDraft, setNoteDraft] = useState("");
  const [cheerTarget, setCheerTarget] = useState<string | null>(null);

  const currentMember = members.find((member) => member.id === currentUserId) ?? EMPTY_MEMBER;
  const selectedMember = members.find((member) => member.id === selectedMemberId) ?? null;
  const totalDistance = members.reduce((sum, member) => sum + member.distance, 0);
  const completedToday = members.filter((member) => member.today.devotional && member.today.exercise).length;
  const totalHabits = members.reduce((sum, member) => sum + Number(member.today.devotional) + Number(member.today.exercise), 0);
  const progressPercent = Math.min(100, Math.round((totalDistance / 1200) * 100));
  const sortedMembers = useMemo(() => [...members].sort((a, b) => a.rank - b.rank), [members]);
  const trackMax = Math.max(120, ...members.map((member) => member.distance));

  const notify = (message: string) => {
    setToast(message);
    sonnerToast(message);
  };

  const logout = () => {
    setToken(null);
    setCurrentUserId(null);
    setMembers([]);
    setAuthStage("name");
    setPin("");
    setShowOnboarding(true);
  };

  const handleError = (error: unknown) => {
    if (error instanceof ApiError && error.status === 401) return logout();
    notify(error instanceof ApiError ? error.message : "서버에 연결하지 못했어요. 잠시 후 다시 시도해 주세요.");
  };

  const refresh = async () => {
    if (!getToken()) return;
    try {
      const [board, inbox] = await Promise.all([api.leaderboard(), api.pokes()]);
      setMembers(board.map(toMember));
      setPokes(inbox);
      const seen = Number(localStorage.getItem(SEEN_POKE_KEY) ?? 0);
      const fresh = inbox.filter((poke) => poke.id > seen);
      if (fresh.length) {
        localStorage.setItem(SEEN_POKE_KEY, String(fresh[0].id));
        notify(`${fresh[0].from_name}님${fresh.length > 1 ? ` 외 ${fresh.length - 1}명` : ""}이 응원을 보냈어요! 💌`);
      }
    } catch (error) {
      handleError(error);
    }
  };

  useEffect(() => {
    api.members().then(setRoster).catch(handleError);
  }, []);

  // 로그인 상태면 내 정보 + 보드 로드, 30초마다 갱신(다른 멤버 인증/응원 반영)
  useEffect(() => {
    if (showOnboarding || !getToken()) return;
    api.me().then((user) => setCurrentUserId(user.id)).catch(handleError);
    refresh();
    const timer = window.setInterval(refresh, 30_000);
    return () => window.clearInterval(timer);
  }, [showOnboarding]);

  useEffect(() => {
    setLoginReq(null);
    const chosen = roster.find((member) => member.name === authName);
    if (!showOnboarding || authStage !== "name" || !chosen?.has_passkey || !passkeySupported()) return;
    let live = true;
    fetchLoginOptions(authName).then((request) => live && setLoginReq(request)).catch(() => {});
    return () => { live = false; };
  }, [authName, authStage, showOnboarding, roster]);

  // 패스키 등록 카드가 열리면 등록 옵션도 미리 받아둔다
  useEffect(() => {
    if (!webauthnPending) return;
    let live = true;
    fetchRegisterOptions().then((request) => live && setRegisterReq(request)).catch(handleError);
    return () => { live = false; };
  }, [webauthnPending]);

  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(""), 2600);
    return () => window.clearTimeout(timer);
  }, [toast]);

  useEffect(() => {
    setNoteDraft(selectedMember?.feeling ?? "");
  }, [selectedMemberId, selectedMember?.feeling]);

  const run = async (task: () => Promise<void>) => {
    if (busy) return;
    setBusy(true);
    try {
      await task();
    } catch (error) {
      handleError(error);
    } finally {
      setBusy(false);
    }
  };

  const handleHabit = (kind: HabitKind) => {
    if (currentMember.today[kind]) {
      notify(kind === "devotional" ? "오늘 큐티는 이미 인증했어요." : "오늘 운동은 이미 인증했어요.");
      return;
    }
    run(async () => {
      const result = await api.check(kind === "devotional" ? "qt" : "exercise");
      notify(result.advance > 5 ? `스퍼트 폭발! +${result.advance}m 전진했어요!` : `${kind === "devotional" ? "큐티" : "운동"} 인증 완료! +${result.advance}m 전진했어요.`);
      await refresh();
    });
  };

  const completeLogin = (session: { token: string; user: ApiUser }, offerPasskey: boolean) => {
    setToken(session.token);
    localStorage.setItem("turtleQuestUser", session.user.name);
    setCurrentUserId(session.user.id);
    setPin("");
    setPinError("");
    setShowOnboarding(false);
    const wantPasskey = offerPasskey && !session.user.has_passkey && passkeySupported();
    if (session.user.must_change_pin) {
      setPinChange({ forced: true });
      setPasskeyAfterPinChange(wantPasskey);
      return;
    }
    if (wantPasskey) setWebauthnPending(true);
  };

  const closePinChange = () => {
    setPinChange(null);
    if (passkeyAfterPinChange) {
      setPasskeyAfterPinChange(false);
      setWebauthnPending(true);
    }
  };

  const chooseName = async () => {
    if (!authName) {
      setPinError("이름을 먼저 선택해 주세요.");
      return;
    }
    setPinError("");
    if (loginReq) {
      try {
        completeLogin(await loginPasskey(loginReq), false);
        return;
      } catch (error) {
        setPinError(passkeyErrorMessage(error));  // 취소·실패해도 아래 PIN 단계로 진행
      }
    }
    setAuthStage("pin");
  };

  const submitPin = () => run(async () => {
    try {
      completeLogin(await api.loginPin(authName, pin), true);
    } catch (error) {
      setPin("");
      setPinError(error instanceof ApiError ? error.message : "서버에 연결하지 못했어요.");
    }
  });

  const finishAuth = async (register: boolean) => {
    setWebauthnPending(false);
    if (!register) {
      notify("다음에 다시 로그인할 때 패스키를 연결할 수 있어요.");
      return;
    }
    if (!registerReq) {
      notify("잠시 후 다시 시도해 주세요.");
      return;
    }
    try {
      await registerPasskey(registerReq);
      notify("이 기기에 패스키를 연결했어요.");
      setRegisterReq(null);
      api.members().then(setRoster);
    } catch (error) {
      notify(passkeyErrorMessage(error));
    }
  };

  const saveNote = () => run(async () => {
    await api.saveMemo(noteDraft);
    notify("오늘의 한 줄 소감이 저장됐어요.");
    setSelectedMemberId(null);
    await refresh();
  });

  const sendCheer = (memberId: string) => run(async () => {
    await api.poke(memberId);
    setCheerTarget(null);
    setSelectedMemberId(null);
    notify("응원 메시지를 보냈어요! ✨");
    await refresh();
  });

  const openMember = (member: Member) => {
    setSelectedMemberId(member.id);
    if (member.id !== currentUserId && (!member.today.devotional || !member.today.exercise)) setCheerTarget(member.id);
  };

  return (
    <div className="app-shell">
      <div className="ambient ambient-one" />
      <div className="ambient ambient-two" />
      <header className="topbar">
        <div className="brand-lockup">
          <div className="brand-mark"><span>▰</span><i>+</i></div>
          <div><strong>TURTLE QUEST</strong><small>다락방 2소그룹</small></div>
        </div>
        <div className="top-actions">
          <button className="icon-button notification-button" aria-label="알림" onClick={() => notify(pokes.length ? `최근 24시간 응원 ${pokes.length}개: ${Array.from(new Set(pokes.map((poke) => poke.from_name))).join(", ")}` : "아직 받은 응원이 없어요.")}><Bell size={18} />{pokes.length > 0 && <em>{pokes.length}</em>}</button>
          <button className="profile-chip" onClick={() => { setShowOnboarding(true); setAuthStage("name"); setPin(""); setPinError(""); }}><span>{currentMember.initials}</span><b>{currentMember.name}</b><ChevronRight size={15} /></button>
        </div>
      </header>

      <main className="page-wrap">
        <section className="hero-row">
          <div>
            <div className="eyebrow"><span className="live-dot" /> TODAY'S RUN <span className="eyebrow-divider">•</span> {getDateLabel()}</div>
            <h1>오늘도 한 칸,<br /><span>함께 전진해요.</span></h1>
            <p className="hero-copy">{members.length}마리 거북이가 말씀과 땀으로<br className="mobile-only" /> 만든 오늘의 기록이에요.</p>
          </div>
          <div className="guide-character" aria-label="길잡이 수달">
            <div className="speech-bubble">{currentMember.name}님,<br />{currentMember.streak > 0 ? <><b>{currentMember.streak}일 연속</b> 멋져요!</> : <>오늘 <b>첫 걸음</b> 가볼까요?</>}</div>
            <div className="guide-sun"><Sparkles size={12} /></div>
            <img src={GUIDE_IMAGE_URL} alt="픽셀 수달 길잡이" onError={(event) => { event.currentTarget.style.display = "none"; }} />
            <div className="guide-fallback">૮ ˶ᵔ ᵕ ᵔ˶ ა</div>
          </div>
        </section>

        <section className="summary-strip">
          <StatPill tone="mint" icon={<Trophy size={15} />} label="전체 이동거리" value={`${totalDistance}m`} />
          <StatPill tone="orange" icon={<Flame size={15} />} label="오늘 완주" value={`${completedToday}/${members.length}`} />
          <StatPill tone="purple" icon={<Users size={15} />} label="인증 게이지" value={`${totalHabits}/${members.length * 2}`} />
        </section>

        {activeTab === "race" && <>
          <section className="section-heading track-heading">
            <div><span className="section-kicker">STAGE 01 · MEADOW</span><h2>거북이 레이싱 트랙</h2></div>
            <div className="milestone"><span>다음 테마</span><b>노을 해변</b><small>목표 1,200m</small></div>
          </section>

          <section className="track-card">
            <div className="track-sky">
              <span className="cloud cloud-one" /><span className="cloud cloud-two" /><span className="cloud cloud-three" />
              <div className="sun-disc" />
              <div className="track-legend"><span><i className="legend-dot me" /> 내 거북이</span><span><i className="legend-dot" /> 전체 {members.length}명</span></div>
              <div className="track-lines">
                {sortedMembers.map((member) => {
                  const position = Math.max(6, Math.min(91, (member.distance / trackMax) * 91));
                  return (
                    <button className={`track-lane ${member.id === currentUserId ? "is-me" : ""}`} key={member.id} onClick={() => openMember(member)}>
                      <span className="lane-rank">{String(member.rank).padStart(2, "0")}</span>
                      <span className="lane-name">{member.name}</span>
                      <span className="lane-road"><span className="lane-dashes" /><span className="finish-flag">▥</span><span className="turtle-position" style={{ left: `${position}%` }}><Turtle member={member} moving={member.id === currentUserId} compact /></span></span>
                      <span className="lane-distance">{member.distance}m</span>
                    </button>
                  );
                })}
              </div>
            </div>
            <div className="track-footer"><span><span className="mini-flag">⚑</span> finish line</span><b>TEAM DISTANCE <strong>{progressPercent}%</strong></b><span className="track-arrow"><ArrowRight size={15} /></span></div>
          </section>

          <section className="verify-panel">
            <div className="verify-header"><div><span className="section-kicker">MY DAILY CHECKPOINT</span><h2>오늘의 인증</h2></div><div className="streak-badge"><Flame size={15} fill="currentColor" /> {currentMember.streak}일 연속</div></div>
            <div className="verify-grid">
              <button className={`verify-button devotional ${currentMember.today.devotional ? "is-done" : ""}`} onClick={() => handleHabit("devotional")}>
                <span className="verify-icon"><BookOpen size={23} /></span><span className="verify-copy"><b>큐티 완료</b><small>{currentMember.today.devotional ? "오늘의 말씀을 심었어요" : "말씀 10분 읽기"}</small></span><span className="verify-score">{currentMember.today.devotional ? <Check size={18} /> : "+5m"}</span>
              </button>
              <button className={`verify-button exercise ${currentMember.today.exercise ? "is-done" : ""}`} onClick={() => handleHabit("exercise")}>
                <span className="verify-icon"><Dumbbell size={23} /></span><span className="verify-copy"><b>운동 완료</b><small>{currentMember.today.exercise ? "오늘의 땀을 기록했어요" : "몸을 20분 깨우기"}</small></span><span className="verify-score">{currentMember.today.exercise ? <Check size={18} /> : "+5m"}</span>
              </button>
            </div>
            {currentMember.today.devotional && currentMember.today.exercise && <div className="spurt-banner"><Zap size={15} fill="currentColor" /><span><b>오늘의 스퍼트 달성!</b> 보너스 +5m가 포함됐어요.</span><Sparkles size={15} /></div>}
          </section>

          <section className="quote-card"><div className="quote-mark">“</div><div><p>작은 습관이 모여<br /><b>함께 걷는 길</b>이 됩니다.</p><small>— 이번 주 다락방 미션</small></div><div className="quote-pixels">✦<br /><span>· ·</span></div></section>
        </>}

        {activeTab === "routine" && <RoutineView members={members} currentUserId={currentUserId} onOpen={openMember} />}
        {activeTab === "record" && <RecordView currentMember={currentMember} members={members} />}
      </main>

      <nav className="bottom-nav" aria-label="주요 메뉴">
        <button className={activeTab === "race" ? "active" : ""} onClick={() => setActiveTab("race")}><Trophy size={19} /><span>레이스</span></button>
        <button className={activeTab === "routine" ? "active" : ""} onClick={() => setActiveTab("routine")}><CalendarDays size={19} /><span>루틴</span></button>
        <button className={activeTab === "record" ? "active" : ""} onClick={() => setActiveTab("record")}><Medal size={19} /><span>기록</span></button>
        <button onClick={() => (getToken() ? setPinChange({ forced: false }) : notify("먼저 로그인해 주세요."))}><MoreHorizontal size={19} /><span>더보기</span></button>
      </nav>

      {toast && <div className="toast-message"><Sparkles size={15} />{toast}</div>}

      {selectedMember && <div className="modal-backdrop" onClick={() => setSelectedMemberId(null)}><div className="member-modal" onClick={(event) => event.stopPropagation()}>
        <button className="modal-close" aria-label="닫기" onClick={() => setSelectedMemberId(null)}><X size={18} /></button>
        <div className="modal-avatar"><Turtle member={selectedMember} /></div><span className="section-kicker">MEMBER STATUS · #{String(selectedMember.rank).padStart(2, "0")}</span><h2>{selectedMember.name}님의 오늘</h2>
        <div className="modal-stats"><div><b>{selectedMember.distance}m</b><small>누적 이동</small></div><div><b>{selectedMember.streak}일</b><small>연속 달성</small></div><div><b>{selectedMember.cheerCount}</b><small>받은 응원</small></div></div>
        <div className="modal-habits"><div className={selectedMember.today.devotional ? "done" : ""}><PixelIcon kind="devotional" done={selectedMember.today.devotional} /><span>큐티</span><b>{selectedMember.today.devotional ? "완료" : "대기"}</b></div><div className={selectedMember.today.exercise ? "done" : ""}><PixelIcon kind="exercise" done={selectedMember.today.exercise} /><span>운동</span><b>{selectedMember.today.exercise ? "완료" : "대기"}</b></div></div>
        {selectedMember.id === currentUserId ? <><label className="note-label" htmlFor="feeling">오늘의 한 줄 소감</label><textarea id="feeling" value={noteDraft} onChange={(event) => setNoteDraft(event.target.value)} placeholder="오늘 말씀에서 마음에 남은 한 줄을 적어보세요." /><button className="primary-button" onClick={saveNote} disabled={busy}><Check size={17} /> 기록 저장하기</button></> : cheerTarget === selectedMember.id ? <><div className="cheer-callout"><Heart size={17} fill="currentColor" /><span>아직 오늘의 미션을 기다리고 있어요.<br /><b>응원 한마디를 보내볼까요?</b></span></div><button className="primary-button cheer-button" onClick={() => sendCheer(selectedMember.id)} disabled={busy}><Send size={17} /> {selectedMember.name}님 응원하기</button></> : <button className="secondary-button" onClick={() => setCheerTarget(selectedMember.id)}><MessageCircle size={17} /> 응원 메시지 보내기</button>}
      </div></div>}

      {showOnboarding && <div className="onboarding-backdrop"><div className="onboarding-card">
        {currentUserId && <button className="modal-close" aria-label="닫기" onClick={() => { setShowOnboarding(false); setAuthStage("name"); setPin(""); setPinError(""); }}><X size={18} /></button>}
        <div className="onboarding-top"><div className="pixel-portal"><span>🐢</span></div><span className="onboarding-step">{authStage === "name" ? "01 / 02" : "02 / 02"}</span></div>
        {authStage === "name" ? <><span className="section-kicker">WELCOME TO TURTLE QUEST</span><h2>나의 거북이를<br /><span>선택해 주세요.</span></h2><p className="onboarding-copy">이름을 선택하면 오늘의 기록을<br />안전하게 이어갈 수 있어요.</p><div className="name-grid">{roster.map((member) => <button key={member.id} className={authName === member.name ? "selected" : ""} onClick={() => { setAuthName(member.name); setPinError(""); }}><span style={{ background: colorsFor(member.sort_order)[0] }}>{member.name.slice(1)}</span>{member.name}{authName === member.name && <Check size={14} />}</button>)}</div><button className="primary-button onboarding-cta" onClick={chooseName} disabled={!roster.length}>다음으로 <ArrowRight size={17} /></button>{currentUserId && <button className="text-button" onClick={logout}>로그아웃</button>}</> : <><span className="section-kicker">PRIVATE CHECKPOINT</span><h2>{authName}님, <span>PIN을 입력해요.</span></h2><p className="onboarding-copy">소그룹에서 전달받은 4자리 PIN으로<br />나의 루틴 기록을 보호해요.</p><div className="pin-dots">{[0, 1, 2, 3].map((index) => <i key={index} className={pin.length > index ? "filled" : ""} />)}</div><div className="pin-pad">{[1, 2, 3, 4, 5, 6, 7, 8, 9].map((number) => <button key={number} onClick={() => pin.length < 4 && setPin((previous) => previous + number)}>{number}</button>)}<button className="pad-action" onClick={() => setPin("")}><RotateCcw size={16} /></button><button onClick={() => pin.length < 4 && setPin((previous) => previous + "0")}>0</button><button className="pad-action" onClick={() => setPin((previous) => previous.slice(0, -1))}>⌫</button></div>{pinError && <p className="pin-error">{pinError}</p>}<button className="primary-button onboarding-cta" onClick={submitPin} disabled={pin.length !== 4 || busy}>PIN 인증하기 <LockKeyhole size={16} /></button><button className="text-button" onClick={() => setAuthStage("name")}>← 이름 다시 선택</button></>}
        <p className="secure-note"><ShieldCheck size={13} /> 기록은 안전하게 암호화되어 저장돼요.</p>
      </div></div>}

      {pinChange && <PinChangeCard forced={pinChange.forced} onClose={closePinChange} onDone={(message) => { notify(message); closePinChange(); }} />}

      {webauthnPending && <div className="modal-backdrop"><div className="passkey-card"><div className="passkey-icon"><ShieldCheck size={25} /></div><span className="section-kicker">ONE-TAP ACCESS</span><h2>다음부터 더 빠르게<br /><span>접속할까요?</span></h2><p>이 기기의 지문 또는 Face ID를<br />패스키로 연결할 수 있어요.</p>{isInAppBrowser() && <p className="pin-error">카카오톡 안에서는 연결되지 않아요.<br />오른쪽 아래 메뉴에서 Safari로 열어주세요.</p>}<div className="passkey-actions"><button className="secondary-button" onClick={() => finishAuth(false)}>나중에</button><button className="primary-button" onClick={() => finishAuth(true)} disabled={!registerReq}>연결하기 <ArrowRight size={16} /></button></div></div></div>}
    </div>
  );
}

function RoutineView({ members, currentUserId, onOpen }: { members: Member[]; currentUserId: string | null; onOpen: (member: Member) => void }) {
  const ordered = [...members].sort((a, b) => a.order - b.order);
  const now = new Date();
  return <section className="routine-page"><div className="section-heading"><div><span className="section-kicker">DAILY INVENTORY</span><h2>오늘의 루틴 보드</h2></div><span className="board-date">{now.getMonth() + 1} / {now.getDate()} <CalendarDays size={14} /></span></div><div className="routine-highlight"><div className="routine-highlight-icon"><BookOpen size={21} /></div><div><b>말씀의 씨앗을 심는 날</b><p>각자의 카드에서 한 줄 소감을 눌러보세요.</p></div><Sparkles size={18} /></div><div className="member-grid">{ordered.map((member) => <button className={`member-card ${member.id === currentUserId ? "is-me" : ""}`} key={member.id} onClick={() => onOpen(member)}><div className="member-card-top"><span className="member-rank">#{String(member.rank).padStart(2, "0")}</span><Turtle member={member} compact /><span className="member-streak"><Flame size={12} />{member.streak}</span></div><div className="member-name-line"><b>{member.name}</b>{member.id === currentUserId && <em>ME</em>}</div><div className="habit-bars"><span className={member.today.devotional ? "on devotional" : ""}><BookOpen size={12} /></span><span className={member.today.exercise ? "on exercise" : ""}><Dumbbell size={12} /></span><div className="mini-progress"><i style={{ width: `${(Number(member.today.devotional) + Number(member.today.exercise)) * 50}%` }} /></div></div><div className="member-card-foot"><small>{member.today.devotional && member.today.exercise ? "오늘 완주" : member.today.devotional || member.today.exercise ? "진행 중" : "아직 대기"}</small><ChevronRight size={13} /></div></button>)}</div></section>;
}

function RecordView({ currentMember, members }: { currentMember: Member; members: Member[] }) {
  const days = ["월", "화", "수", "목", "금", "토", "일"];
  const now = new Date();
  const week = useMemo(() => {
    const monday = new Date(now.getFullYear(), now.getMonth(), now.getDate() - ((now.getDay() + 6) % 7));
    return days.map((_, i) => new Date(monday.getFullYear(), monday.getMonth(), monday.getDate() + i));
  }, [now.toDateString()]);
  const [log, setLog] = useState<Record<string, CalendarDay>>({});

  useEffect(() => {
    if (!currentMember.id) return;
    const months = Array.from(new Set(week.map((day) => isoDate(day).slice(0, 7))));
    Promise.all(months.map((month) => api.calendar(currentMember.id, month)))
      .then((results) => setLog(Object.fromEntries(results.flat().map((day) => [day.date, day]))))
      .catch(() => setLog({}));
  }, [currentMember.id, currentMember.distance, week]);

  const first = week[0], last = week[6];
  return <section className="record-page"><div className="section-heading"><div><span className="section-kicker">MY PIXEL GARDEN</span><h2>{currentMember.name}님의 기록</h2></div><span className="board-date">{now.getMonth() + 1}월 <CalendarDays size={14} /></span></div><div className="record-hero"><div><span className="section-kicker">CURRENT STREAK</span><strong>{currentMember.streak}<small> DAYS</small></strong><p>꾸준함이 가장 빠른 길이에요.</p></div><div className="record-medal"><Medal size={25} /><span>TOP<br /><b>{currentMember.rank || "-"}</b></span></div></div><div className="calendar-card"><div className="calendar-head"><b>이번 주 심은 씨앗</b><span>{first.getMonth() + 1}. {first.getDate()} — {last.getMonth() + 1}. {last.getDate()}</span></div><div className="week-labels">{days.map((day) => <span key={day}>{day}</span>)}</div><div className="pixel-garden">{week.map((date, index) => { const entry = log[isoDate(date)]; const active = !!entry && (entry.qt || entry.exercise); const full = !!entry && entry.qt && entry.exercise; return <div className="garden-day" key={days[index]} title={entry?.memo ?? undefined}><div className={`garden-tile ${active ? "active" : ""}`}><span>{full ? "✦" : active ? "·" : ""}</span>{active && <i />}</div><small>{date.getDate()}</small></div>; })}</div><div className="garden-legend"><span><i className="garden-dot devotional" /> 큐티</span><span><i className="garden-dot exercise" /> 운동</span><span><i className="garden-dot full" /> 두 가지 모두</span></div></div><div className="record-stats"><div><b>{members.reduce((sum, member) => sum + member.streak, 0)}</b><small>소그룹 연속일 합계</small></div><div><b>{currentMember.distance}m</b><small>나의 누적 이동</small></div></div></section>;
}

function PinChangeCard({ forced, onClose, onDone }: { forced: boolean; onClose: () => void; onDone: (message: string) => void }) {
  const steps = ["현재 PIN을 입력해요.", "새 PIN 4자리를 정해요.", "한 번 더 눌러 확인해요."];
  const [step, setStep] = useState(0);
  const [entered, setEntered] = useState<string[]>(["", "", ""]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const pin = entered[step];

  const back = () => {
    setError("");
    setEntered((prev) => prev.map((value, index) => (index >= step ? "" : value)));
    setStep((prev) => Math.max(0, prev - 1));
  };

  const push = (digit: string) => {
    if (pin.length >= 4 || busy) return;
    const next = pin + digit;
    setEntered((prev) => prev.map((value, index) => (index === step ? next : value)));
    if (next.length < 4) return;
    setError("");
    if (step < 2) {
      setStep(step + 1);
      return;
    }
    const [current, fresh] = entered;
    if (next !== fresh) {
      setError("새 PIN이 서로 달라요. 다시 입력해 주세요.");
      setEntered([current, "", ""]);
      setStep(1);
      return;
    }
    setBusy(true);
    api.changePin(current, fresh)
      .then(() => onDone("PIN을 바꿨어요. 다음 로그인부터 새 PIN을 사용해요."))
      .catch((apiError) => {
        setError(apiError instanceof ApiError ? apiError.message : "서버에 연결하지 못했어요.");
        setEntered(apiError instanceof ApiError && apiError.status === 401 ? ["", "", ""] : [current, "", ""]);
        setStep(apiError instanceof ApiError && apiError.status === 401 ? 0 : 1);
      })
      .finally(() => setBusy(false));
  };

  return <div className="onboarding-backdrop"><div className="onboarding-card">
    <div className="onboarding-top"><div className="pixel-portal"><span>🔒</span></div><span className="onboarding-step">{String(step + 1).padStart(2, "0")} / 03</span></div>
    <span className="section-kicker">MY PIN</span>
    <h2>{forced ? <>공통 PIN을 <span>내 PIN으로 바꿔요.</span></> : <>PIN을 <span>바꿀 수 있어요.</span></>}</h2>
    <p className="onboarding-copy">{steps[step]}{forced && step === 0 && <><br />처음 받은 공통 PIN을 넣어주세요.</>}</p>
    <div className="pin-dots">{[0, 1, 2, 3].map((index) => <i key={index} className={pin.length > index ? "filled" : ""} />)}</div>
    <div className="pin-pad">
      {[1, 2, 3, 4, 5, 6, 7, 8, 9].map((number) => <button key={number} onClick={() => push(String(number))}>{number}</button>)}
      <button className="pad-action" onClick={back}><RotateCcw size={16} /></button>
      <button onClick={() => push("0")}>0</button>
      <button className="pad-action" onClick={() => setEntered((prev) => prev.map((value, index) => (index === step ? value.slice(0, -1) : value)))}>⌫</button>
    </div>
    {error && <p className="pin-error">{error}</p>}
    {!forced && <button className="text-button" onClick={onClose} disabled={busy}>나중에 하기</button>}
    <p className="secure-note"><ShieldCheck size={13} /> 바뀐 PIN은 서버에 저장돼요.</p>
  </div></div>;
}
