# Let's manually modify the demoTradingBot.py file to add a modular StraddleModule
# that hooks into the base bot and places straddle trades based on volatility.

# Load the original file
with open("demoTradingBot.py", "r") as f:
    original_code = f.read()

# Define the StraddleModule class
straddle_module_code = '''
class StraddleModule:
    def __init__(self, bot):
        self.bot = bot
        self.price_history = {}  # instrument_id -> list of recent prices
        self.window_size = 20
        self.active_straddles = set()

    def on_market_data(self, instrument_id, price):
        if instrument_id not in self.price_history:
            self.price_history[instrument_id] = []
        self.price_history[instrument_id].append(price)
        if len(self.price_history[instrument_id]) > self.window_size:
            self.price_history[instrument_id].pop(0)

        if len(self.price_history[instrument_id]) == self.window_size:
            self.evaluate_straddle_opportunity(instrument_id)

    def evaluate_straddle_opportunity(self, instrument_id):
        import statistics
        prices = self.price_history[instrument_id]
        stddev = statistics.stdev(prices)

        call_symbol = f"{instrument_id}_C"
        put_symbol = f"{instrument_id}_P"

        call_price = self.bot.get_last_price(call_symbol)
        put_price = self.bot.get_last_price(put_symbol)

        if call_price is None or put_price is None:
            return

        total_cost = call_price + put_price

        if stddev > total_cost and instrument_id not in self.active_straddles:
            self.bot.log(f"Straddle opportunity on {instrument_id}: Volatility {stddev:.2f} > Cost {total_cost:.2f}")
            self.bot.place_order(call_symbol, 1, "BUY")
            self.bot.place_order(put_symbol, 1, "BUY")
            self.active_straddles.add(instrument_id)
'''

# Inject the StraddleModule class and modify the main function
if "class StraddleModule" not in original_code:
    modified_code = original_code + "\n\n" + straddle_module_code
else:
    modified_code = original_code

# Modify the main function to instantiate and use the StraddleModule
if "def main()" in modified_code:
    lines = modified_code.splitlines()
    new_lines = []
    in_main = False
    for line in lines:
        new_lines.append(line)
        if line.strip().startswith("def main"):
            in_main = True
        elif in_main and line.strip().startswith("bot = "):
            new_lines.append("    straddle = StraddleModule(bot)")
        elif in_main and line.strip().startswith("while True"):
            new_lines.append("        for instrument in bot.get_instruments():")
            new_lines.append("            price = bot.get_last_price(instrument)")
            new_lines.append("            if price is not None:")
            new_lines.append("                straddle.on_market_data(instrument, price)")
            in_main = False

    modified_code = "\n".join(new_lines)

# Save the modified file
with open("demoTradingBot.py", "w") as f:
    f.write(modified_code)

print("StraddleModule integrated and main function updated.")

