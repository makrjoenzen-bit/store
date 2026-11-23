# JIT Digital Product Aggregation Platform
## Enterprise Control Plane, Real-Time JIT Engine & Telegram Bot

A production-grade, zero-capital FinTech & E-Commerce platform for selling digital products (AI subscriptions, license keys, accounts) with dynamic supplier aggregation, real-time blockchain payments, and full Web Dashboard control.

---

## 🌟 Key Capabilities

1. **Strict Zero-Capital JIT Engine:**
   - Queries connected supplier APIs in real time.
   - Finds the cheapest in-stock supplier and computes dynamic retail prices:
     $$\text{Final Price} = \text{Cheapest Supplier Cost} + \text{Custom Margin} + \text{Gas Buffer}$$
   - When customer payment is confirmed, instantly transfers **only the wholesale cost** from our self-custody BSC hot wallet (`web3.py`) to the supplier's deposit address on the fly (e.g. VenteBot USDTBSC, 300s window).
   - Executes the purchase via the supplier's API with an `idempotency_key` and delivers the key/account to the customer inside Telegram in under 15 seconds.
   - Retains 100% of your profit margin safe in your own wallet.

2. **Web Admin Dashboard as Central Brain:**
   - **Command Center:** Real-time KPI cards (Revenue, Captured Net Profit, Orders Today, Supplier Health), live WebSocket order stream, hot wallet balance.
   - **Suppliers & Health Monitor:** Add/edit REST API suppliers dynamically from the UI, inspect latency sparklines, monitor circuit breakers.
   - **Master Products & Multi-Supplier Mapping:** Map one internal SKU to multiple supplier product IDs.
   - **Live Price Matrix:** Real-time side-by-side cost arbitrage table showing which supplier is currently winning.
   - **Sham Cash Queue:** Review uploaded local fiat receipts with one-click `[Approve & Fund JIT]` or `[Reject]`.
   - **Bot Live Configuration:** Edit greeting messages and rules in real-time with zero bot restarts.

3. **Pluggable Payment Ingest:**
   - **Direct BSC Web3 Listener:** 0% processing fee, matches incoming USDT BEP-20 transfers via unique 4-decimal amounts (e.g. `$23.0147`).
   - **OxaPay / Cryptomus Gateways:** Toggleable from Dashboard (0.4% fee).
   - **Sham Cash:** Semi-automated with real-time WebSocket alerts and receipt reviews.

4. **Self-Healing & Stranded Deposit Safety:**
   - If a supplier order fails after JIT funds have already landed, the funds are stored in `supplier_credits` and **automatically offset against future orders** routed to that supplier.
   - The customer receives an instant refund to their **Internal Bot Wallet Balance**, eliminating outbound gas waste.

---

## 🏗️ System Architecture

```
[ Next.js Admin Dashboard ] <--- (REST + WebSockets) ---> [ FastAPI API Gateway ]
                                                                 │
                                          ┌──────────────────────┴──────────────────────┐
                                          ▼                                             ▼
                                 [ PostgreSQL 16 ]                               [ Redis 7 ]
                             (State, Ledger, Mappings)                 (Pub/Sub, Locks, ARQ Jobs)
                                                                                        │
               ┌───────────────────────┬────────────────────────┬───────────────────────┤
               ▼                       ▼                        ▼                       ▼
      [ Aiogram 3 Bot ]        [ JIT Executor ]        [ BSC Listener ]       [ Health Monitor ]
    (Storefront + Channel)   (Web3 Outgest Engine)    (On-chain Ingest)     (Circuit Breakers)
```

---

## 🚀 Quick Start & Deployment

### 1. Configure Environment Variables
Copy `.env.example` to `.env` and fill in your secrets:
```bash
cp .env.example .env
```
Key variables to provide:
- `HOT_WALLET_ADDRESS` & `HOT_WALLET_PRIVATE_KEY` (BSC address with a small BNB float for gas, e.g. 0.01 BNB)
- `TELEGRAM_BOT_TOKEN` (from @BotFather)
- `TELEGRAM_PUBLIC_CHANNEL_ID` (e.g. `-1001234567890`)
- `ADMIN_TELEGRAM_ID` (your numeric Telegram ID for instant Sham Cash alerts)

### 2. Launch with Docker Compose
Start all 10 services (Postgres, Redis, Backend, Bot, Workers, Dashboard, Nginx):
```bash
docker-compose up -d --build
```

### 3. Seed Initial Demo Data
Populate default categories, Master SKUs, VenteBot v1.2.0 supplier mapping, pricing rules, and default admin:
```bash
docker-compose exec backend python scripts/seed_initial_data.py
```
Default Dashboard credentials:
- **Username:** `admin`
- **Password:** `admin123456`

### 4. Open the Admin Dashboard
Visit:
```
http://localhost:3000
```
or via the reverse proxy at `http://localhost/`.

---

## 📁 Repository Structure

```
b:\bot\
├── src\
│   ├── api\                  # FastAPI routes, auth, websockets, webhooks
│   ├── bot\                  # Aiogram 3 Telegram bot handlers & keyboards
│   ├── core\
│   │   ├── adapters\         # Supplier Adapter Pattern (VenteBot, Generic REST)
│   │   ├── models\           # SQLAlchemy 2.0 async models (ledger, orders, FSM)
│   │   ├── services\         # Pricing engine, order service, JIT executor
│   │   ├── database.py       # Async engine & sessionmaker
│   │   ├── redis.py          # Redis pub/sub bus & distributed locks
│   │   └── security.py       # Password hashing & Fernet config encryption
│   ├── outgest\              # Web3.py BSC hot wallet & Binance Pay payout engines
│   └── workers\              # ARQ JIT worker, BSC listener, health monitor, channel sync
├── dashboard\                # Next.js 14 App Router Admin Dashboard
├── docker\                   # Nginx reverse proxy configuration
├── scripts\                  # Database seeders and maintenance utilities
├── docker-compose.yml        # Multi-container orchestration
└── requirements.txt          # Python dependencies
```
