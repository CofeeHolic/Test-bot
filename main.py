import os
import pandas as pd
import pandas_ta as ta
import numpy as np
from scipy.signal import argrelextrema
from dhanhq import dhanhq
import time
from datetime import datetime, timedelta
import logging
import telegram

# --- Setup Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- 1. SCRIPT CONFIGURATION ---
TICKERS = [
    "DCBBANK.NS", "GMRP&UI.NS", "EMIL.NS", "GAEL.NS", "PNB.NS", "JAMNAAUTO.NS",
    "NFL.NS", "EMBDL.NS", "SPARC.NS", "BAJAJHFL.NS", "BANKINDIA.NS",
    "LEMONTREE.NS", "STLTECH.NS", "JAIBALAJI.NS", "NTPCGREEN.NS", "NIVABUPA.NS",
    "INOXWIND.NS", "BEPL.NS", "ELECTCAST.NS", "SJVN.NS", "TVSSCS.NS",
    "CANBK.NS", "SBFC.NS", "IRFC.NS", "JAICORPLTD.NS", "SAMMAANCAP.NS",
    "NHPC.NS", "IOC.NS", "ASHOKLEY.NS", "MRPL.NS", "REDTAPE.NS",
    "WELSPUNLIV.NS", "IREDA.NS", "NBCC.NS", "UNIONBANK.NS", "IEX.NS",
    "PRSMJOHNSN.NS", "RBA.NS", "VMM.NS", "LLOYDSENT.NS", "SAIL.NS", "J&KBANK.NS",
    "IDBI.NS", "TEXRAIL.NS", "MOTHERSON.NS", "EDELWEISS.NS", "ZEEL.NS",
    "GMRAIRPORT.NS", "HEMIPROP.NS"
]
INTERVAL = 15  # In minutes. Dhan supports '1', '5', '15', '25', '60'.

# --- 2. STRATEGY AND PORTFOLIO PARAMETERS ---
USE_RELAXED_LOGIC = True
RISK_PER_TRADE_PERCENT = 0.02
RSI_PERIOD = 14
ADX_PERIOD = 14
VOLUME_SMA_PERIOD = 20
ADX_THRESHOLD = 18
VOLUME_MULTIPLIER = 1.2
DIVERGENCE_ORDER = 3
RISK_REWARD_RATIO = 2.0

# --- Credentials ---
# IMPORTANT: For sandbox simulation, paste your credentials directly here.
DHAN_CLIENT_ID = "YOUR_CLIENT_ID_HERE"
DHAN_ACCESS_TOKEN = "YOUR_ACCESS_TOKEN_HERE"

# Optional: For Telegram notifications
TELEGRAM_BOT_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN_HERE"
TELEGRAM_CHAT_ID = "YOUR_TELEGRAM_CHAT_ID_HERE"

# --- Global variables ---
dhan = None
instrument_list_df = None
open_positions = {}  # Tracks open positions: {'TICKER': {'order_id': '...', 'type': 'long'/'short'}}
historical_data_cache = {}  # Stores historical data for each ticker

def get_instrument_list():
    """Downloads and loads the Dhan instrument list."""
    global instrument_list_df
    url = "https://images.dhan.co/api-data/api-scrip-master.csv"
    try:
        logging.info("Downloading instrument list...")
        instrument_list_df = pd.read_csv(url, low_memory=False)
        logging.info("Instrument list downloaded successfully.")
    except Exception as e:
        logging.error(f"Error downloading instrument list: {e}")
        instrument_list_df = None

def get_security_id_and_exchange(ticker):
    """Maps a ticker symbol to its Dhan securityId and exchange segment."""
    if instrument_list_df is None:
        logging.error("Instrument list not loaded.")
        return None, None
    symbol, _, exchange_suffix = ticker.partition('.')
    exchange = 'NSE' if exchange_suffix == 'NS' else 'BSE'
    filtered_df = instrument_list_df[
        (instrument_list_df['SEM_INSTRUMENT_NAME'] == 'EQUITY') &
        (instrument_list_df['SM_SYMBOL_NAME'] == symbol) &
        (instrument_list_df['SEM_EXM_EXCH_ID'] == exchange) &
        (instrument_list_df['SEM_SERIES'] == 'EQ')
    ]
    if not filtered_df.empty:
        security_id = str(filtered_df.iloc[0]['SEM_SECURITY_ID'])
        exchange_segment = filtered_df.iloc[0]['SEM_EXCHANGE_SEGMENT']
        return security_id, exchange_segment
    else:
        logging.warning(f"Security ID not found for {ticker}")
        return None, None

def fetch_historical_data(security_id, exchange_segment, interval):
    """
    Fetches 1-minute historical intraday data for a security for the last 5 days
    and resamples it to the specified interval.
    """
    try:
        # intraday_minute_data provides data for the last 5 trading days.
        to_date = datetime.now()
        from_date = to_date - timedelta(days=5)

        # The python-dhanhq library function for intraday data is intraday_minute_data
        # It fetches 1-minute candles which we will resample.
        response = dhan.intraday_minute_data(
            security_id=security_id,
            exchange_segment=exchange_segment,
            instrument_type='EQUITY',
            expiry_code=0, # Not applicable for equity
            from_date=from_date.strftime('%Y-%m-%d'),
            to_date=to_date.strftime('%Y-%m-%d')
        )

        if response and response.get('status') == 'success' and 'data' in response:
            data = response['data']
            if not data.get('start_Time'): # Check if data is empty
                logging.warning(f"No intraday data returned for {security_id}.")
                return None

            df = pd.DataFrame({
                'Timestamp': pd.to_datetime(data['start_Time'], unit='s'),
                'Open': data['open'],
                'High': data['high'],
                'Low': data['low'],
                'Close': data['close'],
                'Volume': data['volume']
            }).set_index('Timestamp')

            # Resample the 1-minute data to the desired interval
            df_resampled = df.resample(f'{interval}T').agg({
                'Open': 'first',
                'High': 'max',
                'Low': 'min',
                'Close': 'last',
                'Volume': 'sum'
            }).dropna()

            return df_resampled
        else:
            logging.warning(f"Could not fetch data for {security_id}. Response: {response}")
            return None
    except Exception as e:
        logging.error(f"Error fetching historical data for {security_id}: {e}")
        return None

def find_divergence(price, indicator, order=5):
    bullish_divergence = pd.Series(False, index=price.index)
    bearish_divergence = pd.Series(False, index=price.index)
    price_low_indices = argrelextrema(price.values, np.less_equal, order=order)[0]
    price_high_indices = argrelextrema(price.values, np.greater_equal, order=order)[0]
    if len(price_low_indices) >= 2:
        for i in range(1, len(price_low_indices)):
            prev_low_idx, curr_low_idx = price_low_indices[i-1], price_low_indices[i]
            if price.iloc[curr_low_idx] < price.iloc[prev_low_idx] and indicator.iloc[curr_low_idx] > indicator.iloc[prev_low_idx]:
                bullish_divergence.iloc[curr_low_idx] = True
    if len(price_high_indices) >= 2:
        for i in range(1, len(price_high_indices)):
            prev_high_idx, curr_high_idx = price_high_indices[i-1], price_high_indices[i]
            if price.iloc[curr_high_idx] > price.iloc[prev_high_idx] and indicator.iloc[curr_high_idx] < indicator.iloc[prev_high_idx]:
                bearish_divergence.iloc[curr_high_idx] = True
    return bullish_divergence, bearish_divergence

def calculate_indicators(df):
    if df.empty or len(df) < RSI_PERIOD: return df
    df['RSI'] = ta.rsi(df['Close'], length=RSI_PERIOD)
    df['Volume_SMA'] = ta.sma(df['Volume'], length=VOLUME_SMA_PERIOD)
    adx_df = ta.adx(df['High'], df['Low'], df['Close'], length=ADX_PERIOD)
    if adx_df is not None and not adx_df.empty:
        df['ADX'] = adx_df[f'ADX_{ADX_PERIOD}']
        df['DMP'] = adx_df[f'DMP_{ADX_PERIOD}']
        df['DMN'] = adx_df[f'DMN_{ADX_PERIOD}']
    else:
        df[['ADX', 'DMP', 'DMN']] = np.nan
    df.dropna(subset=['RSI', 'Volume_SMA', 'ADX'], inplace=True)
    if df.empty: return df
    bull_div, bear_div = find_divergence(df['Close'], df['RSI'], order=DIVERGENCE_ORDER)
    df['bullish_divergence'] = bull_div
    df['bearish_divergence'] = bear_div
    df['divergence_low'] = pd.Series(np.where(df['bullish_divergence'], df['Low'], np.nan), index=df.index).ffill()
    df['divergence_high'] = pd.Series(np.where(df['bearish_divergence'], df['High'], np.nan), index=df.index).ffill()
    return df

def send_telegram_message(message):
    """Sends a message to the configured Telegram chat."""
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID and "YOUR_TELEGRAM" not in TELEGRAM_BOT_TOKEN:
        try:
            bot = telegram.Bot(token=TELEGRAM_BOT_TOKEN)
            bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message, parse_mode=telegram.ParseMode.MARKDOWN)
            logging.info("Sent Telegram notification.")
        except Exception as e:
            logging.error(f"Failed to send Telegram notification: {e}")

def check_signals_and_trade(ticker, ticker_info, capital):
    if ticker in open_positions:
        logging.info(f"Position already open for {ticker}. Skipping signal check.")
        return

    logging.info(f"Checking for signals on {ticker}...")
    df = historical_data_cache.get(ticker)
    if df is None or len(df) < 2:
        logging.warning(f"Not enough historical data for {ticker} to check for signals.")
        return

    # We check the signal on the second-to-last candle.
    prev_candle = df.iloc[-2]
    current_candle = df.iloc[-1]

    is_bullish_divergence = prev_candle['bullish_divergence']
    is_bearish_divergence = prev_candle['bearish_divergence']
    volume_confirmed = prev_candle['Volume'] > (prev_candle['Volume_SMA'] * VOLUME_MULTIPLIER)

    entry_type = None
    if is_bullish_divergence and volume_confirmed: entry_type = 'long'
    elif is_bearish_divergence and volume_confirmed: entry_type = 'short'

    if entry_type:
        trigger_trade = False
        if USE_RELAXED_LOGIC:
            trigger_trade = True
        else:
            adx_confirmed = prev_candle['ADX'] > ADX_THRESHOLD
            di_cross_confirmed = (prev_candle['DMP'] > prev_candle['DMN']) if entry_type == 'long' else (prev_candle['DMN'] > prev_candle['DMP'])
            if adx_confirmed and di_cross_confirmed: trigger_trade = True

        if trigger_trade:
            logging.info(f"Trade signal found for {ticker}! Type: {entry_type.upper()}")
            # --- Place Trade ---
            entry_price = current_candle['Open']
            stop_loss = prev_candle['divergence_low'] if entry_type == 'long' else prev_candle['divergence_high']

            if pd.isna(stop_loss):
                logging.warning(f"Cannot place trade for {ticker} due to invalid Stop Loss (NaN).")
                return

            risk_per_share = abs(entry_price - stop_loss)
            if risk_per_share <= 0:
                logging.warning(f"Cannot place trade for {ticker}, risk per share is zero or negative.")
                return

            risk_amount = capital * RISK_PER_TRADE_PERCENT
            position_size = int(risk_amount / risk_per_share)
            if position_size == 0:
                logging.warning(f"Position size for {ticker} is zero. Skipping trade.")
                return

            take_profit = entry_price + (risk_per_share * RISK_REWARD_RATIO) if entry_type == 'long' else entry_price - (risk_per_share * RISK_REWARD_RATIO)

            logging.info(f"Placing Bracket Order for {ticker}:")
            logging.info(f"  - Size: {position_size}, Entry: ~{entry_price:.2f}")
            logging.info(f"  - Stop Loss: {stop_loss:.2f}, Take Profit: {take_profit:.2f}")

            try:
                order_response = dhan.place_order(
                    security_id=ticker_info['security_id'],
                    exchange_segment=ticker_info['exchange_segment'],
                    transaction_type='BUY' if entry_type == 'long' else 'SELL',
                    quantity=position_size,
                    order_type='MARKET',
                    product_type='BO',  # Bracket Order
                    price=0, # Market order
                    bo_profit_value=round(abs(take_profit - entry_price), 1),
                    bo_stop_loss_value=round(abs(stop_loss - entry_price), 1)
                )
                logging.info(f"Order response for {ticker}: {order_response}")
                if order_response and order_response.get('status') == 'success' and 'data' in order_response:
                    order_id = order_response['data']['orderId']
                    open_positions[ticker] = {'order_id': order_id, 'type': entry_type}
                    logging.info(f"Successfully placed order for {ticker}, Order ID: {order_id}")

                    # Send Telegram notification
                    trade_notification_msg = (
                        f"✅ *New Trade Signal*\n\n"
                        f"*Ticker:* `{ticker}`\n"
                        f"*Type:* `{entry_type.upper()}`\n"
                        f"*Entry Price:* `{entry_price:.2f}`\n"
                        f"*Stop Loss:* `{stop_loss:.2f}`\n"
                        f"*Take Profit:* `{take_profit:.2f}`\n"
                        f"*Quantity:* `{position_size}`"
                    )
                    send_telegram_message(trade_notification_msg)
                else:
                    logging.error(f"Failed to place order for {ticker}.")

            except Exception as e:
                logging.error(f"Exception when placing order for {ticker}: {e}")

def main():
    """The main function to run the trading bot."""
    global dhan
    logging.info("--- Dhan Trading Bot ---")
    if "YOUR_CLIENT_ID" in DHAN_CLIENT_ID or "YOUR_ACCESS_TOKEN" in DHAN_ACCESS_TOKEN:
        logging.critical("FATAL: Please replace the placeholder credentials in main.py.")
        return

    is_sandbox = True
    api_url = "https://api-sandbox.dhan.co" if is_sandbox else "https://api.dhan.co"
    logging.info(f"Mode: {'Sandbox' if is_sandbox else 'Live Trading'}")

    try:
        dhan = dhanhq(DHAN_CLIENT_ID, DHAN_ACCESS_TOKEN, api_url=api_url)
        fund_limits = dhan.get_fund_limits()
        if fund_limits and fund_limits.get('status') == 'success':
            logging.info("Dhan API client initialized successfully.")
            capital = fund_limits['data']['availabelBalance']
            logging.info(f"Available Balance: {capital}")
            send_telegram_message(
                f"🚀 *Dhan Trading Bot Started*\n\n"
                f"*Mode:* `{'Sandbox' if is_sandbox else 'Live Trading'}`\n"
                f"*Available Capital:* `{capital:.2f}`"
            )
        else:
            logging.error(f"Failed to init Dhan API client. Response: {fund_limits}")
            return
    except Exception as e:
        logging.critical(f"Error initializing Dhan API client: {e}")
        return

    get_instrument_list()
    if instrument_list_df is None:
        logging.critical("FATAL: Could not download instrument list. Halting.")
        return

    ticker_map = {
        ticker: info for ticker in TICKERS
        if (info := dict(zip(['security_id', 'exchange_segment'], get_security_id_and_exchange(ticker)))) and info.get('security_id')
    }

    if not ticker_map:
        logging.critical("FATAL: Could not map any tickers to security IDs. Halting.")
        return

    logging.info("\n-- Ticker to Security ID Mapping --")
    for ticker, data in ticker_map.items():
        logging.info(f"- {ticker}: {data['security_id']} ({data['exchange_segment']})")

    logging.info("\nBot is starting the main loop. Press Ctrl+C to stop.")

    while True:
        try:
            # Check for closed positions (e.g. SL/TP hit)
            # In a real scenario, you'd use websockets or periodically check order book
            # For simplicity, we'll assume BOs handle it. We can add reconciliation logic later.

            current_time = datetime.now()
            if not (current_time.weekday() < 5 and 9 <= current_time.hour < 16):
                 logging.info("Market is closed. Sleeping...")
                 time.sleep(60)
                 continue

            # --- Main Logic Loop ---
            for ticker, ticker_info in ticker_map.items():
                # Fetch/update data
                data_df = fetch_historical_data(ticker_info['security_id'], ticker_info['exchange_segment'], INTERVAL)
                if data_df is not None:
                    historical_data_cache[ticker] = data_df
                    # Calculate indicators
                    historical_data_cache[ticker] = calculate_indicators(historical_data_cache[ticker])
                    # Check for signals and trade
                    check_signals_and_trade(ticker, ticker_info, capital)
                time.sleep(2) # Avoid hitting rate limits

            logging.info(f"Loop finished. Sleeping for {INTERVAL} minutes.")
            time.sleep(INTERVAL * 60)

        except KeyboardInterrupt:
            logging.info("Bot stopped by user.")
            send_telegram_message("🛑 *Dhan Trading Bot Stopped*")
            break
        except Exception as e:
            logging.error(f"An error occurred in the main loop: {e}")
            send_telegram_message(f"🔥 *Bot Error*\n\n`{e}`")
            time.sleep(60) # Wait a minute before retrying

if __name__ == "__main__":
    main()
