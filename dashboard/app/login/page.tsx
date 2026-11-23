"use client";

import React, { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { login } from "../../lib/auth";

function LoginForm() {
  const router = useRouter();
  const search = useSearchParams();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setBusy(true);
    const result = await login(username.trim(), password);
    setBusy(false);
    if (!result.ok) {
      setError(typeof result.detail === "string" ? result.detail : "فشل تسجيل الدخول");
      return;
    }
    const next = search.get("next") || "/";
    router.replace(next.startsWith("/") ? next : "/");
    router.refresh();
  };

  return (
    <div className="w-full max-w-md">
      <div className="mb-8 text-center space-y-2">
        <h1 className="text-2xl font-bold tracking-tight text-white">لوحة التحكم</h1>
        <p className="text-sm text-slate-400">سجّل الدخول لإدارة المتجر</p>
      </div>
      <form
        onSubmit={onSubmit}
        className="rounded-2xl border border-slate-800 bg-slate-900/80 p-6 space-y-4 shadow-xl"
      >
        <div>
          <label className="block text-xs text-slate-400 mb-1.5">اسم المستخدم</label>
          <input
            autoComplete="username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            className="w-full rounded-xl bg-slate-950 border border-slate-700 px-3 py-2.5 text-sm text-white focus:outline-none focus:ring-2 focus:ring-sky-500/40"
            required
          />
        </div>
        <div>
          <label className="block text-xs text-slate-400 mb-1.5">كلمة المرور</label>
          <input
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="w-full rounded-xl bg-slate-950 border border-slate-700 px-3 py-2.5 text-sm text-white focus:outline-none focus:ring-2 focus:ring-sky-500/40"
            required
          />
        </div>
        {error && (
          <div className="text-sm text-rose-300 bg-rose-500/10 border border-rose-500/30 rounded-xl px-3 py-2">
            {error}
          </div>
        )}
        <button
          type="submit"
          disabled={busy}
          className="w-full py-2.5 rounded-xl bg-sky-600 hover:bg-sky-500 text-white text-sm font-bold disabled:opacity-50 transition"
        >
          {busy ? "جاري التحقق…" : "دخول"}
        </button>
      </form>
    </div>
  );
}

export default function LoginPage() {
  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 antialiased flex items-center justify-center p-4">
      <Suspense fallback={<div className="text-slate-400 text-sm">…</div>}>
        <LoginForm />
      </Suspense>
    </div>
  );
}
