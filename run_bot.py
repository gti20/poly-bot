"""
Main entry point for the Polymarket Grok Trader Bot
"""
import asyncio
import sys
from trader import Trader
from config import Settings, load_settings

async def main():
    # 1. Load and Configure Settings
    settings = load_settings()
    
    # --- TESTING OVERRIDES ---
    settings.dry_run = True
    settings.bankroll_usd = 1000.0  # Seed with "paper" money
    settings.min_edge_bits = 0.002  # Lower threshold for testing
    settings.min_liquidity_usd = 0  # See everything
    settings.market_pool_size = 50  # Ensure this is NOT zero
    # -------------------------

    trader = Trader(settings)
    
    # Use the bankroll from settings for the log
    print(f"🚀 Starting bot run...")
    print(f"Bankroll: ${settings.bankroll_usd:.2f} | Dry Run: {settings.dry_run}\n")

    try:
        # 2. Run the strategy
        # We only call run_once. It handles fetching and scoring internally.
        result = await trader.run_once(top_n=10, verbose=True)
        
        # 3. Print Results
        print("\n✅ Bot run completed!")
        print(f"Scored markets: {len(result.scored)}")
        print(f"Placed orders: {result.placed_orders}")
        
        if result.total_position_value > 0:
            print(f"Total Position Value: ${result.total_position_value:.2f}")

    except Exception as e:
        print(f"❌ Error during run: {e}")
        # This helps you see exactly which line failed
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\nBot stopped by user.")
    except Exception as e:
        print(f"\n❌ Fatal error: {e}")

   
