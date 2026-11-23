"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

export default function ShamCashRedirect() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/orders?tab=sham-cash");
  }, [router]);
  return <p className="text-sm text-slate-400">جارٍ التحويل إلى الطلبات…</p>;
}
