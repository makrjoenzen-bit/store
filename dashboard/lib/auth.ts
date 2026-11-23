/** Client auth helpers — cookie is HttpOnly; status via /api/auth/me */

export function getWsUrl(): string {
  if (typeof window === "undefined") return "/ws";
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}/ws`;
}

export async function fetchMe(): Promise<{
  id: string;
  username: string;
  display_name?: string;
  role: string;
} | null> {
  try {
    const res = await fetch("/api/auth/me", { credentials: "include" });
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

export async function login(username: string, password: string): Promise<{ ok: boolean; detail?: string }> {
  try {
    const res = await fetch("/api/auth/login/json", {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      return { ok: false, detail: data.detail || "فشل تسجيل الدخول" };
    }
    return { ok: true };
  } catch {
    return { ok: false, detail: "تعذر الاتصال بالخادم" };
  }
}

export async function logout(): Promise<void> {
  try {
    await fetch("/api/auth/logout", { method: "POST", credentials: "include" });
  } catch {
    /* ignore */
  }
  if (typeof window !== "undefined") {
    window.location.href = "/login";
  }
}
