"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

export default function TreasuryRedirect() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/");
  }, [router]);
  return <p className="text-sm text-slate-400">جارٍ التحويل إلى الرئيسية…</p>;
}
