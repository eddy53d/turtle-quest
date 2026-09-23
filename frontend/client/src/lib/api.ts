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

export async function registerPasskey() {
  const { options, challenge_token } = await call<{ options: any; challenge_token: string }>("/api/auth/webauthn/register-options", { method: "POST" });
  const cred = (await navigator.credentials.create({
    publicKey: {
      ...options,
      challenge: b64uToBuf(options.challenge),
      user: { ...options.user, id: b64uToBuf(options.user.id) },
      excludeCredentials: (options.excludeCredentials ?? []).map((c: any) => ({ ...c, id: b64uToBuf(c.id) })),
    },
  })) as PublicKeyCredential;
  const r = cred.response as AuthenticatorAttestationResponse;
  await call("/api/auth/webauthn/register", {
    body: {
      challenge_token,
      credential: {
        id: cred.id, rawId: bufToB64u(cred.rawId), type: cred.type,
        response: { clientDataJSON: bufToB64u(r.clientDataJSON), attestationObject: bufToB64u(r.attestationObject) },
      },
    },
  });
}

export async function loginPasskey(name: string): Promise<Session> {
  const { options, challenge_token } = await call<{ options: any; challenge_token: string }>("/api/auth/webauthn/verify-options", { body: { name } });
  const cred = (await navigator.credentials.get({
    publicKey: {
      ...options,
      challenge: b64uToBuf(options.challenge),
      allowCredentials: (options.allowCredentials ?? []).map((c: any) => ({ ...c, id: b64uToBuf(c.id) })),
    },
  })) as PublicKeyCredential;
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
}
