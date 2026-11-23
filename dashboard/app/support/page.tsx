"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

export default function SupportRedirect() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/customers?tab=support");
  }, [router]);
  return <p className="text-sm text-slate-400">جارٍ التحويل إلى العملاء…</p>;
}
