"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

export default function MarketingRedirect() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/bot-config?tab=marketing");
  }, [router]);
  return <p className="text-sm text-slate-400">جارٍ التحويل إلى إعدادات البوت…</p>;
}
