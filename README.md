# Dhan Trading Bot

This is a trading bot that uses a divergence strategy to trade on the Dhan platform.

**Note on Sandbox Mode**: By default, the bot runs in sandbox mode using the URL `https://api-sandbox.dhan.co`. To run in live mode, you must change `is_sandbox = True` to `is_sandbox = False` in `main.py`. You will also need to provide live API credentials.

## Setup

1. Clone the repository.
2. Install the dependencies: `pip install -r requirements.txt`
3. **Configure your credentials**:
   Open the `main.py` file and locate the "Credentials" section near the top. Paste your sandbox credentials directly into the placeholder variables:
   ```python
   # --- Credentials ---
   # IMPORTANT: For sandbox simulation, paste your credentials directly here.
   DHAN_CLIENT_ID = "YOUR_CLIENT_ID_HERE"
   DHAN_ACCESS_TOKEN = "YOUR_ACCESS_TOKEN_HERE"

   # Optional: For Telegram notifications
   TELEGRAM_BOT_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN_HERE"
   TELEGRAM_CHAT_ID = "YOUR_TELEGRAM_CHAT_ID_HERE"
   ```

## Running the bot

To run the bot locally:

```bash
python main.py
```

## Deployment on Render

This bot is designed to be deployed on Render as a background worker.

1. Create a new "Background Worker" service on Render.
2. Connect your Git repository.
3. Set the build command to `pip install -r requirements.txt`.
4. Set the start command to `python main.py`.
5. Add the environment variables `DHAN_CLIENT_ID` and `DHAN_ACCESS_TOKEN` in the Render dashboard.
6. Deploy the service.
