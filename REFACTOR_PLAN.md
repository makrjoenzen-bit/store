# Production Store Refactor Plan (Phases 1–4)

Audit-backed 4-phase refactor: cookie JWT admin auth + middleware, Linear-style Arabic RTL dashboard (6 nav sections, zero mock KPIs), Customer/Admin bot HTML+i18n overhaul, and everyday-buyer Mini App (4 tabs, Telegram initData).

**Defaults:** dark slate Linear-inspired palette; collapse Marketing / Sham-Cash / Treasury / Support into six nav sections; keep `/api/v1/reseller/*` for B2B but remove from Mini App UI; protect admin APIs under existing `/api/*` with cookie JWT. Public: `/api/auth/*`, `/api/miniapp/*`, `/api/v1/reseller/*`, `/api/webhooks/*`, `GET /health`, `WS /ws`.

## Phase 1 — Security, Auth & Connectivity
- Create: `dashboard/app/login/page.tsx`, `dashboard/middleware.ts`, `dashboard/lib/auth.ts`
- Modify: `src/config.py`, `.env.example`, `src/api/routes/auth.py`, `src/core/security.py`, `src/api/main.py`, nginx, compose, WS relative paths
- Env: `ADMIN_USERNAME`, `ADMIN_PASSWORD`
- Gate: `npm run build`, health, auth redirect

## Phase 2 — Dashboard UX + Dynamic Suppliers
- 6 nav: الرئيسية، الموردين، المنتجات والأسعار، الطلبات، العملاء والرصيد، إعدادات البوت والقنوات
- Zero mock KPIs; supplier CRUD + Test Connection + Sync
- Gate: build/tsc + mock grep clean

## Phase 3 — Customer & Admin Bots
- Global HTML parse_mode; i18n AR/EN; force-join; simplified customer menu
- Admin whitelist + DB stats / approve-reject / broadcast
- Gate: py_compile bots

## Phase 4 — Mini App
- 4 tabs only (Store, Orders, Balance, Account); remove API/LARA/JIT
- Harden Telegram initData; language sync with Customer Bot
- Gate: final build + greps

## Docker test
```bash
docker compose up -d --build
# http://localhost/login  http://localhost/miniapp  http://localhost/api/health
```
