"""Streamlit dashboard for the Polymarket Grok Trader."""
from __future__ import annotations

import sys
from dataclasses import asdict
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from polymarket_grok_trader.config import load_settings
from polymarket_grok_trader.trader import Trader

st.set_page_config(page_title="Polymarket Grok Trader", layout="wide")
st.title("Polymarket Grok Trader Dashboard")


@st.cache_resource
def get_trader() -> Trader:
    settings = load_settings()
    return Trader(settings)


def display_positions(position_values: list[dict]) -> None:
    if not position_values:
        st.info("No open positions.")
        return
    st.dataframe(position_values, use_container_width=True)


try:
    trader = get_trader()
except Exception as error:
    st.error(f"Failed to initialise trader: {error}")
    st.stop()

settings = trader.settings

# ── session state ────────────────────────────────────────────────────────────
for key in (
    "last_run", "last_status", "last_auth", "last_models",
    "last_stream_log", "last_close",
):
    if key not in st.session_state:
        st.session_state[key] = None

if "last_models" not in st.session_state:
    st.session_state.last_models = []
if "last_stream_log" not in st.session_state:
    st.session_state.last_stream_log = ""
if "running" not in st.session_state:
    st.session_state.running = False
if "pending_action" not in st.session_state:
    st.session_state.pending_action = ""

is_running = bool(st.session_state.running)

# ── sidebar controls ─────────────────────────────────────────────────────────
with st.sidebar:
    st.header("Run Options")
    top_n = st.number_input(
        "Top opportunities to consider",
        min_value=1, max_value=50, value=10, step=1, disabled=is_running,
    )
    verbose_stream = st.checkbox("Stream verbose progress", value=True, disabled=is_running)
    confirm_close_all = st.checkbox(
        "I understand --close-all is irreversible", disabled=is_running
    )

    st.divider()
    st.caption(
        f"**Model:** {settings.grok_model}\n\n"
        f"**Dry run:** {'✅ yes' if settings.dry_run else '🔴 NO — LIVE'}\n\n"
        f"**Kelly multiplier:** {settings.kelly_multiplier}\n\n"
        f"**Min edge:** {settings.min_edge}"
    )

# ── action buttons ────────────────────────────────────────────────────────────
col_run, col_status, col_close, col_auth, col_models = st.columns(5)

with col_run:
    run_clicked = st.button("▶ Run Strategy", type="primary", use_container_width=True, disabled=is_running)
with col_status:
    status_clicked = st.button("📊 Status", use_container_width=True, disabled=is_running)
with col_close:
    close_clicked = st.button(
        "⛔ Close All", use_container_width=True,
        disabled=is_running or (not confirm_close_all),
    )
with col_auth:
    auth_clicked = st.button("🔑 Auth Check", use_container_width=True, disabled=is_running)
with col_models:
    models_clicked = st.button("🤖 Grok Models", use_container_width=True, disabled=is_running)

for clicked, action in [
    (run_clicked, "run"),
    (status_clicked, "status"),
    (close_clicked, "close"),
    (auth_clicked, "auth"),
    (models_clicked, "models"),
]:
    if clicked and not is_running:
        st.session_state.running = True
        st.session_state.pending_action = action
        st.rerun()

# ── execute pending action ────────────────────────────────────────────────────
if st.session_state.running:
    st.warning("⏳ Job running… controls are temporarily disabled.")
    action = st.session_state.pending_action

    if action:
        live_log = st.empty()
        try:
            if action == "run":
                with st.spinner("Running strategy…"):
                    st.session_state.last_stream_log = ""

                    def _on_progress(message: str) -> None:
                        if not verbose_stream:
                            return
                        current = st.session_state.last_stream_log
                        st.session_state.last_stream_log = f"{current}\n{message}".strip()
                        live_log.code(st.session_state.last_stream_log)

                    st.session_state.last_run = trader.run_once(
                        top_n=int(top_n),
                        stream_progress=False,
                        progress_callback=_on_progress,
                    )

            elif action == "status":
                with st.spinner("Fetching portfolio status…"):
                    st.session_state.last_status = trader.get_portfolio_status()

            elif action == "close":
                with st.spinner("Closing all positions…"):
                    st.session_state.last_close = trader.close_all_positions()

            elif action == "auth":
                with st.spinner("Running auth diagnostics…"):
                    st.session_state.last_auth = trader.polymarket.auth_diagnostics()

            elif action == "models":
                with st.spinner("Fetching Grok models…"):
                    st.session_state.last_models = trader.grok.list_models()

        finally:
            st.session_state.pending_action = ""
            st.session_state.running = False
            st.rerun()

# ── display results ───────────────────────────────────────────────────────────

st.subheader("Current Configuration")
st.write({
    "polymarket_host": settings.polymarket_host,
    "grok_model": settings.grok_model,
    "max_markets": settings.max_markets,
    "market_pool_size": settings.market_pool_size,
    "max_positions_per_run": settings.max_positions_per_run,
    "dry_run": settings.dry_run,
    "kelly_multiplier": settings.kelly_multiplier,
    "min_edge": settings.min_edge,
})

if st.session_state.last_stream_log:
    st.subheader("Last Run Log")
    st.code(st.session_state.last_stream_log)

if st.session_state.last_run is not None:
    run = st.session_state.last_run
    st.subheader("Last Run Summary")
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Candidates", len(run.scored))
    m2.metric("Attempted Orders", run.attempted_orders)
    m3.metric("Placed Orders", run.placed_orders)
    m4.metric("Attempted Exits", run.attempted_exits)
    m5.metric("Placed Exits", run.placed_exits)
    st.caption(
        f"Bankroll: ${run.effective_bankroll_usd:.2f} (source={run.bankroll_source}) "
        f"| Position value: ${run.total_position_value:.2f}"
    )

    top_rows = [
        {
            "question": e.market.question,
            "side": e.side,
            "edge": round(e.edge, 4),
            "divergence": round(e.divergence, 4),
            "liquidity_usd": round(e.market.liquidity, 2),
            "fair_yes_prob": round(e.fair_odds.fair_yes_probability, 4),
            "yes_price": round(e.market.yes_price, 4),
            "no_price": round(e.market.no_price, 4),
            "rationale": e.fair_odds.rationale,
            "condition_id": e.market.condition_id,
        }
        for e in run.scored[: int(top_n)]
    ]
    st.subheader(f"Top {top_n} Opportunities")
    if top_rows:
        st.dataframe(top_rows, use_container_width=True)
    else:
        st.info("No scored opportunities this run.")

    st.subheader("Evaluation Logs")
    st.code("\n".join(run.evaluation_logs))

    st.subheader("Positions (mark-to-bid)")
    display_positions([asdict(pv) for pv in run.position_values])

if st.session_state.last_status is not None:
    status = st.session_state.last_status
    st.subheader("Portfolio Status")
    a1, a2, a3 = st.columns(3)
    a1.metric("Available USDC", f"${status.available_balance_usd:.2f}")
    a2.metric("Position Value", f"${status.total_position_value:.2f}")
    a3.metric("Total Equity", f"${status.available_balance_usd + status.total_position_value:.2f}")
    display_positions([asdict(pv) for pv in status.position_values])

if st.session_state.last_close is not None:
    close = st.session_state.last_close
    st.subheader("Close-All Result")
    st.write({
        "attempted_exits": close.attempted_exits,
        "placed_exits": close.placed_exits,
        "skipped_tickers": close.skipped_tickers,
    })

if st.session_state.last_auth is not None:
    st.subheader("Auth Diagnostics")
    st.json(st.session_state.last_auth)

if st.session_state.last_models:
    st.subheader("Available Grok Models")
    st.write(st.session_state.last_models)
