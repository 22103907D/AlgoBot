import requests
import time
import hmac
import hashlib
import math
import json
import os
from datetime import datetime
from typing import Dict, List, Tuple, Optional

# Import the necessary components from the tradingview-ta library
from tradingview_ta import get_multiple_analysis, Interval

# ========== CONFIGURATION ==========
BASE_URL = "https://mock-api.roostoo.com"
API_KEY = ""
SECRET_KEY = ""

# File to store purchase prices
PORTFOLIO_FILE = "portfolio.json"
TP_THRESHOLD = 1.06  
SL_THRESHOLD = 0.97  
STRONG_SELL_THRESHOLD = 13  # Minimum number of indicators for a strong SELL signal

# Cycle timings
QUICK_TP_SL_CHECK_INTERVAL = 15  # Check TP/SL every 30 seconds
FULL_TRADING_CYCLE_INTERVAL = 600  # Full trading cycle every 15 minutes (900 seconds)

TRADING_PAIRS = [
    "BTC/USD", "ETH/USD", "ZEC/USD", "SOL/USD", "XRP/USD", "BNB/USD", "DOGE/USD",
    "ASTER/USD", "WLFI/USD", "TRUMP/USD", "NEAR/USD", "ICP/USD", "LTC/USD",
    "FIL/USD", "XPL/USD", "SUI/USD", "PUMP/USD", "VIRTUAL/USD", "LINK/USD",
    "TRX/USD", "ENA/USD", "HBAR/USD", "UNI/USD", "FET/USD", "ADA/USD", "ZEN/USD",
    "TAO/USD", "AVAX/USD", "PEPE/USD", "AAVE/USD", "DOT/USD", "PENGU/USD",
    "PAXG/USD", "WLD/USD", "XLM/USD", "SEI/USD", "EIGEN/USD", "ARB/USD", "S/USD",
    "APT/USD", "CAKE/USD", "CRV/USD", "LINEA/USD", "BONK/USD", "WIF/USD",
    "FORM/USD", "TON/USD", "EDEN/USD", "SHIB/USD", "POL/USD", "FLOKI/USD",
    "ONDO/USD", "SOMI/USD", "AVNT/USD", "HEMI/USD", "PLUME/USD", "MIRA/USD",
    "CFX/USD", "PENDLE/USD", "BIO/USD", "TUT/USD", "OPEN/USD", "OMNI/USD",
    "BMT/USD", "1000CHEEMS/USD", "LISTA/USD"
]

CYCLE_INTERVAL = 300
RESERVE_CASH = 20000 # Lowered to a more reasonable value for demonstration

# Caches for efficiency: populated at runtime
PAIR_PRECISION = {}
PAIR_EXCHANGE_CACHE = {}

# ========== PORTFOLIO MANAGEMENT ==========
def load_portfolio() -> Dict[str, Dict]:
    """Load purchase history from file."""
    if os.path.exists(PORTFOLIO_FILE):
        try:
            with open(PORTFOLIO_FILE, 'r') as f:
                return json.load(f)
        except Exception as e:
            log(f"⚠️ Error loading portfolio: {e}")
    return {}

def save_portfolio(portfolio: Dict):
    """Save purchase history to file."""
    try:
        with open(PORTFOLIO_FILE, 'w') as f:
            json.dump(portfolio, f, indent=2)
    except Exception as e:
        log(f"⚠️ Error saving portfolio: {e}")

def update_portfolio_on_buy(pair: str, quantity: float, current_price: float, portfolio: Dict):
    """Update portfolio after a successful buy."""
    coin = pair.split('/')[0]
    if coin not in portfolio:
        portfolio[coin] = {'buy_price': 0, 'quantity': 0}
    
    # Calculate weighted average purchase price
    old_qty = portfolio[coin]['quantity']
    old_price = portfolio[coin]['buy_price']
    new_total_qty = old_qty + quantity
    
    if new_total_qty > 0:
        portfolio[coin]['buy_price'] = (old_price * old_qty + current_price * quantity) / new_total_qty
    
    portfolio[coin]['quantity'] = new_total_qty
    portfolio[coin]['timestamp'] = datetime.now().isoformat()
    save_portfolio(portfolio)
    log(f"  📝 Portfolio updated: {coin} avg buy price=${portfolio[coin]['buy_price']:.8f}, qty={new_total_qty:.8f}")

def get_holdings_to_sell_for_tp_sl(balance_data: Dict, technicals: Dict[str, Dict], portfolio: Dict) -> Tuple[Dict[str, float], Dict[str, str]]:
    """
    Check current holdings against TP/SL thresholds.
    Returns: (positions_to_sell, sell_reasons)
    """
    positions = get_current_positions(balance_data)
    positions_to_sell = {}
    sell_reasons = {}
    
    # Calculate and log account balances
    cash_balance = get_total_cash_balance(balance_data)
    
    # Get ticker for portfolio valuation
    ticker_data = get_ticker()
    portfolio_value = get_total_portfolio_value(balance_data, ticker_data)
    total_balance = cash_balance + portfolio_value
    
    log("\n🎯 TECHNICAL-CYCLE TP/SL CHECK - Analyzing holdings...")
    log(f"  💰 Cash Balance: ${cash_balance:.2f} | Portfolio Value: ${portfolio_value:.2f} | Total: ${total_balance:.2f}")
    
    for coin, quantity in positions.items():
        pair = f"{coin}/USD"
        
        if coin not in portfolio:
            log(f"  ℹ️ {coin}: No purchase record in portfolio. Skipping.")
            continue
        
        if pair not in technicals:
            log(f"  ℹ️ {coin}: No technical data available. Skipping.")
            continue
        
        buy_price = portfolio[coin]['buy_price']
        current_price = technicals[pair]['price']
        price_ratio = current_price / buy_price if buy_price > 0 else 1.0
        price_change_pct = (price_ratio - 1) * 100
        
        if price_ratio >= TP_THRESHOLD:
            log(f"  🎯 {coin}: TP HIT! | Buy: ${buy_price:.8f} | Current: ${current_price:.8f} | Change: +{price_change_pct:.2f}% | ACTION: WILL SELL")
            positions_to_sell[pair] = quantity
            sell_reasons[pair] = "TP"
        elif price_ratio <= SL_THRESHOLD:
            log(f"  🛑 {coin}: SL HIT! | Buy: ${buy_price:.8f} | Current: ${current_price:.8f} | Change: {price_change_pct:.2f}% | ACTION: WILL SELL")
            positions_to_sell[pair] = quantity
            sell_reasons[pair] = "SL"
        else:
            log(f"  ✅ {coin}: SAFE | Buy: ${buy_price:.8f} | Current: ${current_price:.8f} | Change: {price_change_pct:+.2f}% | ACTION: HOLD")
    
    return positions_to_sell, sell_reasons

def quick_tp_sl_check_and_sell(portfolio: Dict) -> float:
    """
    Quick TP/SL check that runs frequently (every 30 seconds).
    Fetches current balance and market prices via API.
    Checks all holdings against TP/SL thresholds.
    Executes sells immediately if thresholds are hit.
    """
    balance_data = get_balance()
    if not balance_data:
        log("⚠️ Could not fetch balance for TP/SL check. Skipping.")
        return 0.0
    
    positions = get_current_positions(balance_data)
    if not positions:
        log("ℹ️ No positions to check for TP/SL.")
        return 0.0
    
    total_received = 0.0
    
    # Get ticker data for all held coins
    ticker_data = get_ticker()
    if not ticker_data or not ticker_data.get('Data'):
        log("⚠️ Could not fetch market prices. Skipping TP/SL check.")
        return 0.0
    
    # Calculate and log account balances
    cash_balance = get_total_cash_balance(balance_data)
    portfolio_value = get_total_portfolio_value(balance_data, ticker_data)
    total_balance = cash_balance + portfolio_value
    
    log(f"\n🎯 QUICK TP/SL CHECK - Analyzing {len(positions)} positions...")
    log(f"  💰 Cash Balance: ${cash_balance:.2f} | Portfolio Value: ${portfolio_value:.2f} | Total: ${total_balance:.2f}")
    
    for coin, quantity in positions.items():
        pair = f"{coin}/USD"
        
        if coin not in portfolio or portfolio[coin]['buy_price'] <= 0:
            log(f"  ℹ️ {coin}: No purchase record in portfolio. Skipping.")
            continue
        
        # Get current market price from ticker
        market_data = ticker_data.get('Data', {}).get(pair)
        if not market_data:
            log(f"  ⚠️ {coin}: No market data available. Skipping.")
            continue
        
        current_price = market_data.get('LastPrice', 0)
        if current_price <= 0:
            log(f"  ⚠️ {coin}: Invalid price data. Skipping.")
            continue
        
        buy_price = portfolio[coin]['buy_price']
        price_ratio = current_price / buy_price if buy_price > 0 else 1.0
        price_change_pct = (price_ratio - 1) * 100
        
        # Check TP threshold
        if price_ratio >= TP_THRESHOLD:
            log(f"  🎯 {coin}: TP HIT! | Buy: ${buy_price:.8f} | Current: ${current_price:.8f} | Change: +{price_change_pct:.2f}%")
            precision = PAIR_PRECISION.get(pair)
            if precision is not None:
                factor = 10 ** precision
                adjusted_quantity = math.floor(quantity * factor) / factor
                if adjusted_quantity > 0:
                    log(f"      💥 EXECUTING TP SELL: {adjusted_quantity} {coin} @ ${current_price:.8f}")
                    success, err_msg, order_detail = place_order(pair, 'SELL', adjusted_quantity)
                    if success:
                        usd_received = order_detail.get('UnitChange', 0)
                        total_received += usd_received
                        
                        # Calculate P&L
                        pl_amount = usd_received - (buy_price * adjusted_quantity)
                        pl_pct = ((current_price - buy_price) / buy_price) * 100
                        pl_emoji = "📈" if pl_amount >= 0 else "📉"
                        pl_sign = "+" if pl_amount >= 0 else ""
                        log(f"      ✅ TP EXECUTED: Sold {adjusted_quantity} {coin} | Revenue: ${usd_received:.2f} | {pl_emoji} P&L: {pl_sign}${pl_amount:.2f} ({pl_sign}{pl_pct:.2f}%)")
                        
                        portfolio[coin]['quantity'] = 0
                        save_portfolio(portfolio)
                    else:
                        log(f"      ❌ TP FAILED: {err_msg}")
            time.sleep(0.5)
        
        # Check SL threshold
        elif price_ratio <= SL_THRESHOLD:
            log(f"  🛑 {coin}: SL HIT! | Buy: ${buy_price:.8f} | Current: ${current_price:.8f} | Change: {price_change_pct:.2f}%")
            precision = PAIR_PRECISION.get(pair)
            if precision is not None:
                factor = 10 ** precision
                adjusted_quantity = math.floor(quantity * factor) / factor
                if adjusted_quantity > 0:
                    log(f"      💥 EXECUTING SL SELL: {adjusted_quantity} {coin} @ ${current_price:.8f}")
                    success, err_msg, order_detail = place_order(pair, 'SELL', adjusted_quantity)
                    if success:
                        usd_received = order_detail.get('UnitChange', 0)
                        total_received += usd_received
                        
                        # Calculate P&L
                        pl_amount = usd_received - (buy_price * adjusted_quantity)
                        pl_pct = ((current_price - buy_price) / buy_price) * 100
                        pl_emoji = "📈" if pl_amount >= 0 else "📉"
                        pl_sign = "+" if pl_amount >= 0 else ""
                        log(f"      ✅ SL EXECUTED: Sold {adjusted_quantity} {coin} | Revenue: ${usd_received:.2f} | {pl_emoji} P&L: {pl_sign}${pl_amount:.2f} ({pl_sign}{pl_pct:.2f}%)")
                        
                        portfolio[coin]['quantity'] = 0
                        save_portfolio(portfolio)
                    else:
                        log(f"      ❌ SL FAILED: {err_msg}")
            time.sleep(0.5)
        
        # No TP/SL hit - log the status
        else:
            log(f"  ✅ {coin}: SAFE | Buy: ${buy_price:.8f} | Current: ${current_price:.8f} | Change: {price_change_pct:+.2f}%")
    
    if total_received > 0:
        log(f"\n💰 Quick TP/SL Check Complete - Total Revenue: ${total_received:.2f}")
    
    return total_received

# ========== UTILITY & API FUNCTIONS ==========
def log(message: str):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}")

def get_timestamp():
    return str(int(time.time() * 1000))

def get_signed_headers(payload: dict = None):
    if payload is None: payload = {}
    payload['timestamp'] = get_timestamp()
    total_params = "&".join(f"{k}={v}" for k, v in sorted(payload.items()))
    signature = hmac.new(SECRET_KEY.encode('utf-8'), total_params.encode('utf-8'), hashlib.sha256).hexdigest()
    return {'RST-API-KEY': API_KEY, 'MSG-SIGNATURE': signature, 'Content-Type': 'application/x-www-form-urlencoded'}, total_params

def get_exchange_info() -> Optional[Dict]:
    url = f"{BASE_URL}/v3/exchangeInfo"
    try:
        res = requests.get(url, timeout=10)
        res.raise_for_status()
        if 'TradePairs' in res.json(): return res.json()
    except requests.exceptions.RequestException as e:
        log(f"❌ Network Error in get_exchange_info: {e}")
    return None

def get_balance() -> Optional[Dict]:
    url = f"{BASE_URL}/v3/balance"
    headers, params_str = get_signed_headers({})
    params_dict = {'timestamp': params_str.split('=')[1]}
    try:
        res = requests.get(url, headers=headers, params=params_dict, timeout=10)
        res.raise_for_status()
        response_json = res.json()
        if not response_json.get('Success'):
            log(f"❌ API Error in get_balance: {response_json.get('ErrMsg', 'N/A')}")
            return None
        return response_json
    except requests.exceptions.RequestException as e:
        log(f"❌ Network Error in get_balance: {e}")
        return None
    return None

def get_ticker(pair: Optional[str] = None) -> Optional[Dict]:
    """Get market ticker data for one or all pairs."""
    url = f"{BASE_URL}/v3/ticker"
    params = {'timestamp': get_timestamp()}
    if pair:
        params['pair'] = pair
    try:
        res = requests.get(url, params=params, timeout=10)
        res.raise_for_status()
        response_json = res.json()
        if not response_json.get('Success'):
            log(f"❌ API Error in get_ticker: {response_json.get('ErrMsg', 'N/A')}")
            return None
        return response_json
    except requests.exceptions.RequestException as e:
        log(f"❌ Network Error in get_ticker: {e}")
        return None

def place_order(pair: str, side: str, quantity: float) -> Tuple[bool, str, Optional[Dict]]:
    url = f"{BASE_URL}/v3/place_order"
    payload = {'pair': pair, 'side': side.upper(), 'type': 'MARKET', 'quantity': str(quantity)}
    headers, total_params = get_signed_headers(payload)
    try:
        res = requests.post(url, headers=headers, data=total_params, timeout=10)
        res.raise_for_status()
        response_json = res.json()
        if response_json.get('Success'): return True, "", response_json.get('OrderDetail')
        return False, response_json.get('ErrMsg', 'Unknown API error'), None
    except requests.exceptions.RequestException as e:
        return False, f"Network Error: {e}", None
    return False, "Unexpected error in place_order", None

# ========== SETUP FUNCTION ==========
def load_exchange_rules():
    log("📋 Loading exchange trading rules...")
    info = get_exchange_info()
    if info and info.get('TradePairs'):
        for pair, details in info['TradePairs'].items():
            precision = details.get('AmountPrecision')
            if precision is not None:
                PAIR_PRECISION[pair] = int(precision)
        log(f"✅ Successfully loaded trading rules for {len(PAIR_PRECISION)} pairs.")
        return True
    else:
        log("❌ CRITICAL: Could not load exchange trading rules. Bot cannot continue.")
        return False

# MODIFIED: get_all_technicals now uses caching for efficiency
def get_all_technicals(pairs: List[str]) -> Dict[str, Dict]:
    log("📊 Fetching all technical indicators...")
    final_technicals = {}
    
    # --- Step 1: Segregate pairs into cached and non-cached ---
    pairs_in_cache = {}
    pairs_to_discover = []
    for pair in pairs:
        if pair in PAIR_EXCHANGE_CACHE:
            exchange = PAIR_EXCHANGE_CACHE[pair]
            if exchange not in pairs_in_cache:
                pairs_in_cache[exchange] = []
            pairs_in_cache[exchange].append(pair)
        else:
            pairs_to_discover.append(pair)

    # --- Step 2: Process cached pairs with targeted requests ---
    if pairs_in_cache:
        log("  ⚙️ Using cache for faster lookups...")
        for exchange, cached_pairs in pairs_in_cache.items():
            log(f"    - Fetching {len(cached_pairs)} known pairs from {exchange}...")
            symbols = [f"{exchange}:{p.replace('/', '')}" for p in cached_pairs]
            try:
                results = get_multiple_analysis("crypto", Interval.INTERVAL_30_MINUTES, symbols, timeout=20)
                for pair in cached_pairs:
                    key = f"{exchange}:{pair.replace('/', '')}"
                    if results.get(key) and results[key].summary:
                        analysis, summary = results[key], results[key].summary; signal = summary.get('RECOMMENDATION', 'NEUTRAL')
                        if "BUY" in signal: signal = "BUY"
                        elif "SELL" in signal: signal = "SELL"
                        final_technicals[pair] = {'price': analysis.indicators['close'], 'sell': summary.get('SELL', 0), 'neutral': summary.get('NEUTRAL', 0), 'buy': summary.get('BUY', 0), 'signal': signal, 'score': summary.get('BUY', 0) - summary.get('SELL', 0)}
            except Exception as e:
                log(f"    - ⚠️ Could not fetch from {exchange} cache, will rediscover. Error: {e}")
                pairs_to_discover.extend(cached_pairs) # Add back to discovery list on error

    # --- Step 3: Discover any new or previously unfound pairs ---
    if pairs_to_discover:
        log(f"  🔍 Discovering {len(pairs_to_discover)} new/unfound pairs...")
        exchanges_to_try = ["BINANCE", "COINBASE", "KUCOIN", "KRAKEN", "BITSTAMP", "BYBIT", "CRYPTOCOM"]
        pairs_left_to_find = list(pairs_to_discover)
        for exchange in exchanges_to_try:
            if not pairs_left_to_find: break
            symbols = [f"{exchange}:{p.replace('/', '')}" for p in pairs_left_to_find]
            try:
                results = get_multiple_analysis("crypto", Interval.INTERVAL_30_MINUTES, symbols, timeout=20)
                if not results: continue
                newly_found_this_exchange = []
                for pair in pairs_left_to_find:
                    key = f"{exchange}:{pair.replace('/', '')}"
                    if results.get(key) and results[key].summary:
                        PAIR_EXCHANGE_CACHE[pair] = exchange # Add to cache for next time!
                        analysis, summary = results[key], results[key].summary; signal = summary.get('RECOMMENDATION', 'NEUTRAL')
                        if "BUY" in signal: signal = "BUY"
                        elif "SELL" in signal: signal = "SELL"
                        final_technicals[pair] = {'price': analysis.indicators['close'], 'sell': summary.get('SELL', 0), 'neutral': summary.get('NEUTRAL', 0), 'buy': summary.get('BUY', 0), 'signal': signal, 'score': summary.get('BUY', 0) - summary.get('SELL', 0)}
                        newly_found_this_exchange.append(pair)
                if newly_found_this_exchange:
                    log(f"    - Discovered {len(newly_found_this_exchange)} pairs on {exchange}.")
                    pairs_left_to_find = [p for p in pairs_left_to_find if p not in newly_found_this_exchange]
            except Exception: pass
    return final_technicals

# ========== TRADING LOGIC ==========
def get_current_positions(balance_data: Dict) -> Dict[str, float]:
    positions = {}
    if balance_data and balance_data.get('Success'):
        for coin, amounts in balance_data.get('SpotWallet', {}).items():
            if coin != 'USD' and (total := amounts.get('Free', 0) + amounts.get('Lock', 0)) > 1e-8:
                positions[coin] = total
    return positions

def get_total_cash_balance(balance_data: Dict) -> float:
    """Get total available cash (USD) from balance data."""
    if balance_data and balance_data.get('Success'):
        return balance_data.get('SpotWallet', {}).get('USD', {}).get('Free', 0)
    return 0.0

def get_total_portfolio_value(balance_data: Dict, ticker_data: Dict) -> float:
    """Calculate total portfolio value in USD (all holdings at current market price)."""
    total_value = 0.0
    positions = get_current_positions(balance_data)
    
    if not ticker_data or not ticker_data.get('Data'):
        return 0.0
    
    ticker_dict = ticker_data.get('Data', {})
    for coin, quantity in positions.items():
        pair = f"{coin}/USD"
        market_data = ticker_dict.get(pair)
        if market_data:
            current_price = market_data.get('LastPrice', 0)
            if current_price > 0:
                total_value += quantity * current_price
    
    return total_value

def rank_pairs_by_technicals(technicals: Dict[str, Dict]) -> List[Tuple[str, Dict]]:
    return sorted(technicals.items(), key=lambda x: x[1]['score'], reverse=True)

def execute_sells(positions: Dict[str, float], technicals: Dict[str, Dict], portfolio: Dict) -> float:
    log("\n💰 EXECUTING SELLS...")
    total_received = 0.0
    total_pl = 0.0
    for coin, quantity in positions.items():
        pair = f"{coin}/USD"
        if pair not in technicals or technicals[pair]['signal'] != 'SELL':
            continue
        # Check for STRONG sell signal (high number of indicators agreeing)
        sell_count = technicals[pair].get('sell', 0)
        if sell_count < STRONG_SELL_THRESHOLD:
            log(f"  ℹ️ {coin}: SELL signal too weak ({sell_count}/{STRONG_SELL_THRESHOLD} indicators). Skipping.")
            continue
        precision = PAIR_PRECISION.get(pair)
        if precision is None: log(f"    ⚠️ No precision rule for {pair}. Skipping sell."); continue
        factor = 10 ** precision
        adjusted_quantity = math.floor(quantity * factor) / factor
        if adjusted_quantity > 0:
            log(f"  🔴 Selling {adjusted_quantity} {coin} (STRONG SELL: {sell_count} indicators)")
            success, err_msg, order_detail = place_order(pair, 'SELL', adjusted_quantity)
            if success:
                usd_received = order_detail.get('UnitChange', 0)
                total_received += usd_received
                
                # Calculate P&L
                if coin in portfolio and portfolio[coin]['buy_price'] > 0:
                    buy_price = portfolio[coin]['buy_price']
                    sell_price = usd_received / adjusted_quantity if adjusted_quantity > 0 else 0
                    pl_amount = usd_received - (buy_price * adjusted_quantity)
                    pl_pct = ((sell_price - buy_price) / buy_price) * 100
                    total_pl += pl_amount
                    
                    pl_emoji = "📈" if pl_amount >= 0 else "📉"
                    pl_sign = "+" if pl_amount >= 0 else ""
                    log(f"    ✅ SUCCESS! | Sold for approx. ${usd_received:.2f} | {pl_emoji} P&L: {pl_sign}${pl_amount:.2f} ({pl_sign}{pl_pct:.2f}%)")
                else:
                    log(f"    ✅ SUCCESS! | Sold for approx. ${usd_received:.2f}")
                
                # Remove from portfolio
                if coin in portfolio:
                    portfolio[coin]['quantity'] = 0
                    save_portfolio(portfolio)
            else:
                log(f"    ❌ FAILED to sell {pair}: {err_msg}")
            time.sleep(1)
    log(f"  💵 Total cash received from sells: ${total_received:.2f}")
    if total_pl != 0:
        pl_emoji = "📈" if total_pl >= 0 else "📉"
        pl_sign = "+" if total_pl >= 0 else ""
        log(f"  {pl_emoji} Total P&L from sells: {pl_sign}${total_pl:.2f}")
    return total_received

def execute_tp_sl_sells(positions_to_sell: Dict[str, float], sell_reasons: Dict[str, str], portfolio: Dict) -> float:
    """Execute sells for TP/SL targets."""
    if not positions_to_sell:
        log("\n✅ No TP/SL targets hit. Proceeding with normal trading.")
        return 0.0
    
    log(f"\n💥 EXECUTING TP/SL SELLS ({len(positions_to_sell)} positions)...")
    total_received = 0.0
    total_pl = 0.0
    
    for pair, quantity in positions_to_sell.items():
        coin = pair.split('/')[0]
        reason = sell_reasons.get(pair, "UNKNOWN")
        precision = PAIR_PRECISION.get(pair)
        
        if precision is None:
            log(f"    ⚠️ No precision rule for {pair}. Skipping sell.")
            continue
        
        factor = 10 ** precision
        adjusted_quantity = math.floor(quantity * factor) / factor
        
        if adjusted_quantity > 0:
            log(f"  💥 Selling {adjusted_quantity} {coin} ({reason})")
            success, err_msg, order_detail = place_order(pair, 'SELL', adjusted_quantity)
            if success:
                usd_received = order_detail.get('UnitChange', 0)
                total_received += usd_received
                
                # Calculate P&L
                if coin in portfolio and portfolio[coin]['buy_price'] > 0:
                    buy_price = portfolio[coin]['buy_price']
                    sell_price = usd_received / adjusted_quantity if adjusted_quantity > 0 else 0
                    pl_amount = usd_received - (buy_price * adjusted_quantity)
                    pl_pct = ((sell_price - buy_price) / buy_price) * 100
                    total_pl += pl_amount
                    
                    pl_emoji = "📈" if pl_amount >= 0 else "📉"
                    pl_sign = "+" if pl_amount >= 0 else ""
                    log(f"    ✅ SUCCESS! | Sold for approx. ${usd_received:.2f} | {pl_emoji} P&L: {pl_sign}${pl_amount:.2f} ({pl_sign}{pl_pct:.2f}%)")
                else:
                    log(f"    ✅ SUCCESS! | Sold for approx. ${usd_received:.2f}")
                
                # Remove from portfolio
                if coin in portfolio:
                    portfolio[coin]['quantity'] = 0
                    save_portfolio(portfolio)
            else:
                log(f"    ❌ FAILED to sell {pair}: {err_msg}")
            time.sleep(1)
    
    log(f"  💵 Total cash received from TP/SL sells: ${total_received:.2f}")
    if total_pl != 0:
        pl_emoji = "📈" if total_pl >= 0 else "📉"
        pl_sign = "+" if total_pl >= 0 else ""
        log(f"  {pl_emoji} Total P&L from TP/SL sells: {pl_sign}${total_pl:.2f}")
    return total_received

def execute_weak_sell_rebalancing(positions: Dict[str, float], technicals: Dict[str, Dict], portfolio: Dict) -> float:
    """
    Sell positions with weak SELL signals (9+ indicators) to fund strong BUY opportunities.
    This is part of the dynamic rebalancing strategy.
    """
    WEAK_SELL_THRESHOLD = 8
    log("\n🔄 EXECUTING WEAK SELL REBALANCING (9+ indicators)...")
    total_received = 0.0
    total_pl = 0.0
    
    for coin, quantity in positions.items():
        pair = f"{coin}/USD"
        if pair not in technicals:
            continue
        
        sell_count = technicals[pair].get('sell', 0)
        if sell_count < WEAK_SELL_THRESHOLD:
            continue
        
        # This coin has a weak SELL signal (9+ indicators) - sell it for rebalancing
        precision = PAIR_PRECISION.get(pair)
        if precision is None:
            log(f"    ⚠️ No precision rule for {pair}. Skipping.")
            continue
        
        factor = 10 ** precision
        adjusted_quantity = math.floor(quantity * factor) / factor
        
        if adjusted_quantity > 0:
            log(f"  🔴 Rebalancing: Selling {adjusted_quantity} {coin} (WEAK SELL: {sell_count} indicators)")
            success, err_msg, order_detail = place_order(pair, 'SELL', adjusted_quantity)
            if success:
                usd_received = order_detail.get('UnitChange', 0)
                total_received += usd_received
                
                # Calculate P&L
                if coin in portfolio and portfolio[coin]['buy_price'] > 0:
                    buy_price = portfolio[coin]['buy_price']
                    sell_price = usd_received / adjusted_quantity if adjusted_quantity > 0 else 0
                    pl_amount = usd_received - (buy_price * adjusted_quantity)
                    pl_pct = ((sell_price - buy_price) / buy_price) * 100
                    total_pl += pl_amount
                    
                    pl_emoji = "📈" if pl_amount >= 0 else "📉"
                    pl_sign = "+" if pl_amount >= 0 else ""
                    log(f"    ✅ REBALANCE SELL SUCCESS! | Sold for approx. ${usd_received:.2f} | {pl_emoji} P&L: {pl_sign}${pl_amount:.2f} ({pl_sign}{pl_pct:.2f}%)")
                else:
                    log(f"    ✅ REBALANCE SELL SUCCESS! | Sold for approx. ${usd_received:.2f}")
                
                # Remove from portfolio
                if coin in portfolio:
                    portfolio[coin]['quantity'] = 0
                    save_portfolio(portfolio)
            else:
                log(f"    ❌ REBALANCE SELL FAILED: {err_msg}")
            time.sleep(1)
    
    if total_received > 0:
        log(f"  💵 Total cash from weak sell rebalancing: ${total_received:.2f}")
        if total_pl != 0:
            pl_emoji = "📈" if total_pl >= 0 else "📉"
            pl_sign = "+" if total_pl >= 0 else ""
            log(f"  {pl_emoji} Total P&L from weak sell rebalancing: {pl_sign}${total_pl:.2f}")
    else:
        log(f"  ℹ️ No weak SELL signals found for rebalancing.")
    
    return total_received

def execute_buys(available_cash: float, ranked_pairs: List[Tuple[str, Dict]], portfolio: Dict, positions: Dict[str, float], technicals: Dict[str, Dict]):
    log("\n💰 EXECUTING BUYS...")
    # Filter for STRONG BUY signals only (14+ indicators)
    buy_signals = [(p, d) for p, d in ranked_pairs if d['signal'] == 'BUY' and d.get('buy', 0) >= STRONG_SELL_THRESHOLD]
    
    if not buy_signals: 
        log("  ℹ️ No STRONG BUY signals found (need 14+ indicators)."); 
        return
    
    # If we have strong BUYs, trigger weak sell rebalancing to fund them
    log(f"\n  🎯 Found {len(buy_signals)} STRONG BUY signals! Initiating rebalancing...")
    cash_from_weak_sells = execute_weak_sell_rebalancing(positions, technicals, portfolio)
    available_cash += cash_from_weak_sells
    
    cash_to_invest = available_cash - RESERVE_CASH
    if cash_to_invest <= 10: 
        log(f"  ℹ️ Cash to invest (${cash_to_invest:.2f}) is too low after rebalancing."); 
        return
    
    log(f"  📈 Deploying {len(buy_signals)} STRONG BUY signals with ${cash_to_invest:.2f}")
    # Exponential allocation: top rank gets most, steep dropoff for others
    num_buys = len(buy_signals)
    exponential_weights = [2 ** (num_buys - i - 1) for i in range(num_buys)]
    total_weight = sum(exponential_weights)
    for i, (pair, data) in enumerate(buy_signals):
        allocation = (exponential_weights[i] / total_weight) * cash_to_invest
        if allocation < 10: continue
        price = data.get('price')
        if not price or price <= 0: continue
        ideal_quantity = allocation / price
        precision = PAIR_PRECISION.get(pair)
        if precision is None: log(f"    ⚠️ No precision rule for {pair}. Skipping buy."); continue
        factor = 10 ** precision
        adjusted_quantity = math.floor(ideal_quantity * factor) / factor
        if adjusted_quantity > 0:
            buy_count = data.get('buy', 0)
            log(f"  🟢 Buying {pair} (Rank #{i+1}, {buy_count} indicators) | Alloc: ${allocation:.2f} | Qty: {adjusted_quantity}")
            success, err_msg, order_detail = place_order(pair, 'BUY', adjusted_quantity)
            if success:
                filled_qty = order_detail.get('FilledQuantity', 0)
                log(f"    ✅ SUCCESS! | Bought {filled_qty:.8f} {pair.split('/')[0]}")
                # Update portfolio with purchase price
                if filled_qty > 0:
                    update_portfolio_on_buy(pair, filled_qty, price, portfolio)
            else:
                log(f"    ❌ FAILED to buy {pair}: {err_msg}")
            time.sleep(1)

# ========== MAIN BOT LOGIC ==========
# ========== MAIN BOT LOGIC ==========
def trading_cycle(portfolio: Dict):
    log("\n" + "="*70 + "\n🤖 FULL TRADING CYCLE (30M Technicals)\n" + "="*70)
    
    balance_data = get_balance()
    if not balance_data: log("❌ CRITICAL: Failed to get balance. Skipping cycle."); return
    available_cash = balance_data.get('SpotWallet', {}).get('USD', {}).get('Free', 0)
    log(f"💵 Starting cash balance from API: ${available_cash:.2f}")
    positions = get_current_positions(balance_data)
    log(f"📊 Current Positions: {len(positions)} coins")
    if positions:
        for coin, qty in positions.items(): log(f"    - {coin}: {qty:.8f}")
    
    # Get technical data
    technicals = get_all_technicals(TRADING_PAIRS)
    if not technicals: log("❌ No technical data retrieved. Skipping cycle."); return
    
    # STEP 1: Check and execute TP/SL sells BEFORE normal buys
    positions_to_sell, sell_reasons = get_holdings_to_sell_for_tp_sl(balance_data, technicals, portfolio)
    cash_from_tp_sl = execute_tp_sl_sells(positions_to_sell, sell_reasons, portfolio)
    available_cash += cash_from_tp_sl
    
    # STEP 2: Normal sell signals
    ranked_pairs = rank_pairs_by_technicals(technicals)
    log("\n📊 TOP 10 TECHNICAL SIGNALS:")
    for i, (pair, data) in enumerate(ranked_pairs[:10], 1):
        log(f"  #{i}. {pair}: Score={data['score']}, Signal={data['signal']}")
    cash_from_sells = execute_sells(positions, technicals, portfolio)
    available_cash += cash_from_sells
    total_cash_for_buys = available_cash
    log(f"💵 Total cash available for buys: ${total_cash_for_buys:.2f}")
    
    # STEP 3: Execute buys (with portfolio tracking and rebalancing)
    execute_buys(total_cash_for_buys, ranked_pairs, portfolio, positions, technicals)
    
    if final_balance := get_balance():
        log(f"\n💰 Cycle Complete - Final API Cash Balance: ${final_balance.get('SpotWallet', {}).get('USD', {}).get('Free', 0):.2f}")
    log("="*70)

def main():
    log("🚀 ROOSTOO TRADINGVIEW ALGO TRADING BOT STARTED")
    if not load_exchange_rules(): return
    log("🤖 Bot is now in LIVE API mode, using official precision rules and exchange caching.")
    log(f"📋 Monitoring {len(TRADING_PAIRS)} pairs.")
    log(f"⚡ Quick TP/SL check every {QUICK_TP_SL_CHECK_INTERVAL} seconds")
    log(f"📊 Full trading cycle every {FULL_TRADING_CYCLE_INTERVAL} seconds ({FULL_TRADING_CYCLE_INTERVAL/60:.0f} minutes) using 30m technicals")
    
    portfolio = load_portfolio()
    quick_check_count = 0
    full_cycle_count = 0
    time_since_full_cycle = FULL_TRADING_CYCLE_INTERVAL  # Start at interval to trigger full cycle immediately
    
    while True:
        try:
            current_time = time.time()
            
            # Check if it's time for full trading cycle (every FULL_TRADING_CYCLE_INTERVAL seconds)
            # Run at startup (time_since_full_cycle will be >= FULL_TRADING_CYCLE_INTERVAL initially)
            if time_since_full_cycle >= FULL_TRADING_CYCLE_INTERVAL:
                full_cycle_count += 1
                log(f"\n{'='*70}")
                log(f"📊 FULL TRADING CYCLE #{full_cycle_count} (30M Technicals)")
                log(f"{'='*70}")
                trading_cycle(portfolio)
                time_since_full_cycle = 0
            
            # Quick TP/SL check (runs every QUICK_TP_SL_CHECK_INTERVAL seconds)
            log(f"\n{'='*70}")
            quick_check_count += 1
            log(f"⚡ QUICK TP/SL CHECK #{quick_check_count}")
            quick_tp_sl_check_and_sell(portfolio)
            
            # Update timer
            time_since_full_cycle += QUICK_TP_SL_CHECK_INTERVAL
            
            log(f"⏳ Waiting {QUICK_TP_SL_CHECK_INTERVAL} seconds...")
            log(f"   (Next full cycle in {FULL_TRADING_CYCLE_INTERVAL - time_since_full_cycle} seconds)")
            time.sleep(QUICK_TP_SL_CHECK_INTERVAL)
            
        except KeyboardInterrupt:
            log("\n⚠️ Bot stopped by user. Exiting.")
            break
        except Exception as e:
            log(f"❌ An unhandled error occurred: {e}")
            log("⏳ Waiting 10 seconds before retrying...")
            time.sleep(10)

if __name__ == "__main__":
    main()