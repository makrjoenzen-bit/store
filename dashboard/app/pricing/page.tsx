"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

export default function PricingRedirect() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/products?tab=pricing");
  }, [router]);
  return <p className="text-sm text-slate-400">جارٍ التحويل إلى المنتجات والأسعار…</p>;
}
