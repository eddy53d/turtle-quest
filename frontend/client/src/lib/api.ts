// FastAPI 백엔드 클라이언트. VITE_API_URL 예: https://turtle-quest-api.onrender.com
const BASE = (import.meta.env.VITE_API_URL ?? "http://localhost:8000").replace(/\/$/, "");
const TOKEN_KEY = "turtleQuestToken";

export type ApiUser = { id: string; name: string; sort_order: number; has_passkey: boolean; must_change_pin?: boolean };
export type BoardRow = {
  rank: number; id: string; name: string; sort_order: number; total_distance: number; streak: number;
  today: { qt: boolean; exercise: boolean }; memo: string; cheers: number;
};
export type CalendarDay = { date: string; qt: boolean; exercise: boolean; memo: string | null };
export type Poke = { id: number; from_name: string; created_at: string };

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

export const getToken = () => localStorage.getItem(TOKEN_KEY);
export const setToken = (token: string | null) => token ? localStorage.setItem(TOKEN_KEY, token) : localStorage.removeItem(TOKEN_KEY);

async function call<T>(path: string, init: { method?: string; body?: unknown } = {}): Promise<T> {
  const token = getToken();
  const res = await fetch(`${BASE}${path}`, {
    method: init.method ?? (init.body ? "POST" : "GET"),
    headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: init.body ? JSON.stringify(init.body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = typeof data.detail === "string" ? data.detail : "잠시 후 다시 시도해 주세요.";
    throw new ApiError(res.status, detail);
  }
  return data as T;
}

type Session = { token: string; user: ApiUser };

export const api = {
  members: () => call<ApiUser[]>("/api/members"),
  me: () => call<ApiUser>("/api/me"),
  loginPin: (name: string, pin: string) => call<Session>("/api/auth/login-pin", { body: { name, pin } }),
  leaderboard: () => call<BoardRow[]>("/api/race/leaderboard"),
  check: (type: "qt" | "exercise") =>
    call<{ advance: number; total_distance: number; streak: number }>("/api/activity/check", { body: { type } }),
  changePin: (current_pin: string, new_pin: string) => call("/api/auth/change-pin", { body: { current_pin, new_pin } }),
  saveMemo: (memo: string) => call("/api/activity/memo", { method: "PUT", body: { memo } }),
  calendar: (userId: string, month: string) => call<CalendarDay[]>(`/api/activity/${userId}/calendar?month=${month}`),
  poke: (toUserId: string) => call("/api/social/poke", { body: { to_user_id: toUserId } }),
  pokes: () => call<Poke[]>("/api/social/pokes"),
};

// ── WebAuthn: 서버 JSON(base64url) ↔ 브라우저 ArrayBuffer 변환 ──────────────
const b64uToBuf = (s: string) => Uint8Array.from(atob(s.replace(/-/g, "+").replace(/_/g, "/")), c => c.charCodeAt(0)).buffer;
const bufToB64u = (b: ArrayBuffer) => btoa(Array.from(new Uint8Array(b), c => String.fromCharCode(c)).join("")).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

export const passkeySupported = () => typeof window !== "undefined" && !!window.PublicKeyCredential;

/** 카카오톡·인스타 등 인앱 브라우저는 패스키(WebAuthn)를 막아둔 경우가 많다. */
export const isInAppBrowser = () =>
  typeof navigator !== "undefined" && /KAKAOTALK|Instagram|FBAN|FBAV|NAVER\(inapp|Line\//i.test(navigator.userAgent);

export type PasskeyRequest = { options: any; challenge_token: string };

// Safari(iOS/macOS)는 navigator.credentials 호출이 사용자 탭과 같은 실행 흐름에 있어야 한다.
// 서버에서 옵션을 받아오는 fetch를 기다린 뒤 호출하면 그 사이 사용자 제스처가 만료돼 NotAllowedError가 난다.
// 그래서 옵션은 미리 받아두고(fetch*Options), 버튼을 누르는 순간엔 곧바로 create/get을 호출한다.
export const fetchRegisterOptions = () => call<PasskeyRequest>("/api/auth/webauthn/register-options", { method: "POST" });
export const fetchLoginOptions = (name: string) => call<PasskeyRequest>("/api/auth/webauthn/verify-options", { body: { name } });

export function registerPasskey({ options, challenge_token }: PasskeyRequest) {
  const created = navigator.credentials.create({
    publicKey: {
      ...options,
      challenge: b64uToBuf(options.challenge),
      user: { ...options.user, id: b64uToBuf(options.user.id) },
      excludeCredentials: (options.excludeCredentials ?? []).map((c: any) => ({ ...c, id: b64uToBuf(c.id) })),
    },
  });
  return created.then((credential) => {
    const cred = credential as PublicKeyCredential;
    const r = cred.response as AuthenticatorAttestationResponse;
    return call("/api/auth/webauthn/register", {
      body: {
        challenge_token,
        credential: {
          id: cred.id, rawId: bufToB64u(cred.rawId), type: cred.type,
          response: { clientDataJSON: bufToB64u(r.clientDataJSON), attestationObject: bufToB64u(r.attestationObject) },
        },
      },
    });
  });
}

export function loginPasskey({ options, challenge_token }: PasskeyRequest): Promise<Session> {
  const got = navigator.credentials.get({
    publicKey: {
      ...options,
      challenge: b64uToBuf(options.challenge),
      allowCredentials: (options.allowCredentials ?? []).map((c: any) => ({ ...c, id: b64uToBuf(c.id) })),
    },
  });
  return got.then((credential) => {
    const cred = credential as PublicKeyCredential;
    const r = cred.response as AuthenticatorAssertionResponse;
    return call<Session>("/api/auth/webauthn/verify", {
      body: {
        challenge_token,
        credential: {
          id: cred.id, rawId: bufToB64u(cred.rawId), type: cred.type,
          response: {
            clientDataJSON: bufToB64u(r.clientDataJSON), authenticatorData: bufToB64u(r.authenticatorData),
            signature: bufToB64u(r.signature), userHandle: r.userHandle ? bufToB64u(r.userHandle) : null,
          },
        },
      },
    });
  });
}

/** 취소·시간초과·미지원을 사람이 읽을 수 있는 문구로. */
export function passkeyErrorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  const name = error instanceof Error ? error.name : "";
  if (name === "NotAllowedError") {
    return isInAppBrowser()
      ? "카카오톡 등 앱 안의 브라우저에서는 지문·Face ID를 쓸 수 없어요. Safari나 크롬으로 열어주세요."
      : "패스키 연결을 취소했거나 시간이 지났어요. 다시 시도해 주세요.";
  }
  if (name === "InvalidStateError") return "이 기기에는 이미 패스키가 등록돼 있어요.";
  if (name === "SecurityError") return "이 주소에서는 패스키를 쓸 수 없어요. 관리자에게 알려주세요.";
  if (name === "NotSupportedError") return "이 기기는 패스키를 지원하지 않아요. PIN으로 로그인해 주세요.";
  return error instanceof Error ? `패스키 오류: ${error.name}` : "패스키를 사용할 수 없어요.";
}
