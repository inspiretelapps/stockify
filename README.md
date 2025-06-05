# Stockify Discord Bot

Stockify is a Python-based Discord bot designed to streamline the process of inventorying electronic devices. Users can upload images of devices (or their labels/boxes) to a designated Discord channel along with a client name. The bot then uses OpenAI's GPT-4o model to analyze the image(s), extract device details (Make, Model, Serial Number, Part Number, MAC Address), and logs this information into a specified Google Sheet. It can process multiple image attachments in a single message and multiple items within each image.

## Features

* **Discord Integration:** Monitors a specific Discord channel for messages containing image attachments.
* **AI-Powered Image Analysis:** Utilizes OpenAI GPT-4o to extract device information from images.
    * Identifies Make, Model, Serial Number, Part Number (generic, Dell P/N, VPN), and MAC Address.
    * Capable of detecting multiple distinct items within a single image.
* **Handles Multiple Attachments:** Can process several image attachments in one Discord message.
* **MAC Address Vendor Lookup:** If the 'Make' is not identified by AI, it attempts to find the vendor using the MAC address via the `macvendors.com` API.
* **Data Logging:** Appends extracted information, along with a timestamp, Discord user, client name, and image URL, to a Google Sheet.
* **Automatic Header Management:** Ensures the Google Sheet has the correct header row.
* **User Feedback:** Provides detailed processing status and results directly in the Discord channel.
* **Timezone Aware:** Timestamps are recorded in SAST (Africa/Johannesburg).
* **Robust Error Handling:** Includes checks for missing configurations, API errors, and image processing issues, providing informative messages.

## Prerequisites

Before you begin, ensure you have the following:

1.  **Python 3.8+** installed.
2.  A **Discord Bot Token**.
    * Create a bot application on the [Discord Developer Portal](https://discord.com/developers/applications).
    * Enable **Privileged Gateway Intents**:
        * `MESSAGE CONTENT INTENT` (essential for reading message text).
        * `SERVER MEMBERS INTENT` (recommended, though not strictly used by all commands here).
    * Invite the bot to your server with necessary permissions (Read Messages, Send Messages, Attach Files, Read Message History).
3.  An **OpenAI API Key** with access to GPT-4o (or your preferred model, which might require code changes).
4.  A **Google Cloud Platform (GCP) Project**:
    * Enable the **Google Sheets API**.
    * Create a **Service Account** and download its credentials as `credentials.json`.
    * A **Google Sheet ID** from the URL of the sheet you want to use (e.g., `https://docs.google.com/spreadsheets/d/THIS_IS_THE_ID/edit`).
    * Share the Google Sheet with the service account's email address (found in `credentials.json`) giving it "Editor" permissions.
5.  A **MACVendors API Token** from [macvendors.com](https://macvendors.com/api). (Free tier available).
6.  The **Target Discord Channel ID** where the bot will operate. (Enable Developer Mode in Discord, then right-click the channel and "Copy ID").

## Setup and Installation

1.  **Clone the Repository (or download the script):**
    ```bash
    git clone <your-repo-url>
    cd <your-repo-directory>
    ```

2.  **Install Dependencies:**
    Create a `requirements.txt` file with the following content:
    ```txt
    discord.py
    python-dotenv
    openai
    google-api-python-client
    google-auth-oauthlib
    google-auth-httplib2
    requests
    pytz
    ```
    Then install them:
    ```bash
    pip install -r requirements.txt
    ```

3.  **Place Google Credentials:**
    Put your downloaded `credentials.json` file in the same directory as `stock_bot.py`.

4.  **Create `.env` File:**
    Create a file named `.env` in the same directory as `stock_bot.py` and add your API keys and configuration details. **Do not commit this file to version control if it contains sensitive information.**

    ```env
    DISCORD_BOT_TOKEN="YOUR_DISCORD_BOT_TOKEN"
    OPENAI_API_KEY="YOUR_OPENAI_API_KEY"
    GOOGLE_SHEET_ID="YOUR_GOOGLE_SHEET_ID"
    MACVENDORS_API_TOKEN="YOUR_MACVENDORS_API_TOKEN"
    TARGET_DISCORD_CHANNEL_ID="YOUR_TARGET_DISCORD_CHANNEL_ID"
    ```
    Replace the placeholder values with your actual credentials and IDs.

## Configuration

* **`.env` file:** This is the primary method for configuration (see step 4 above).
* **`SHEET_NAME` (in script):** Defaults to `'Stockify'`. You can change this directly in the script if needed.
    ```python
    SHEET_NAME = 'Stockify'
    ```
* **`EXPECTED_HEADER` (in script):** Defines the columns in the Google Sheet. If you modify this, ensure your processing logic aligns.
    ```python
    EXPECTED_HEADER = ["Timestamp", "Discord User", "Client Name", "Make", "Model", "Serial Number", "Part Number", "MAC Address", "Image URL"]
    ```
* **OpenAI Model (in script):** Defaults to `"gpt-4o"`. Change if necessary.
    ```python
    model="gpt-4o",
    ```

## Running the Bot

Once all dependencies are installed and the `.env` file is configured:

```bash
python stock_bot.py
