import time
from cli import main as run_strategy   # runs your existing CLI logic

print("🚀 Starting Polymarket Grok Trader (continuous mode)...")
print("Press Ctrl+C to stop\n")

count = 0
while True:
    count += 1
    print(f"\n=== Run #{count} @ {time.strftime('%H:%M:%S')} ===")
    
    try:
        # Run one cycle (same as python cli.py --top 10)
        run_strategy()   # or customize with arguments if needed
    except Exception as e:
        print(f"Error in run: {e}")
    
    print("Waiting 10 minutes until next run...\n")
    time.sleep(600)   # 10 minutes