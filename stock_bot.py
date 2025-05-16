import discord
from discord.ext import commands
import os
import asyncio
from dotenv import load_dotenv
import openai
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
import datetime
import requests
import io
import base64
import json
import pytz # Import pytz for timezone conversion

# --- Load Environment Variables ---
load_dotenv()
DISCORD_BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
GOOGLE_SHEET_ID = os.getenv('GOOGLE_SHEET_ID')

try:
    TARGET_DISCORD_CHANNEL_ID = int(os.getenv('TARGET_DISCORD_CHANNEL_ID'))
except (ValueError, TypeError):
    print("Error: TARGET_DISCORD_CHANNEL_ID is not set or is not a valid integer in your .env file.")
    exit()

# --- OpenAI Setup ---
if OPENAI_API_KEY:
    openai.api_key = OPENAI_API_KEY
else:
    print("Error: OPENAI_API_KEY not found in .env file.")
    exit()

# --- Google Sheets Setup ---
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
SERVICE_ACCOUNT_FILE = 'credentials.json'

if not os.path.exists(SERVICE_ACCOUNT_FILE):
    print(f"Error: Google credentials file '{SERVICE_ACCOUNT_FILE}' not found.")
    exit()

creds = None
try:
    creds = Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE, scopes=SCOPES)
except Exception as e:
    print(f"Error loading Google credentials: {e}")
    exit()

try:
    google_sheets_service = build('sheets', 'v4', credentials=creds)
except Exception as e:
    print(f"Error building Google Sheets service: {e}")
    exit()

SHEET_NAME = 'Sheet1'
EXPECTED_HEADER = ["Timestamp", "Discord User", "Client Name", "Make", "Model", "Serial Number", "Part Number", "MAC Address", "Image URL"]

# --- Discord Bot Setup ---
intents = discord.Intents.default()
intents.messages = True
intents.message_content = True
intents.guilds = True

bot = commands.Bot(command_prefix='!', intents=intents)

@bot.event
async def on_ready():
    print(f'{bot.user.name} has connected to Discord!')
    print(f"Monitoring channel ID: {TARGET_DISCORD_CHANNEL_ID}")
    await set_sheet_header_if_needed()

async def set_sheet_header_if_needed():
    try:
        range_to_check = f"{SHEET_NAME}!A1:{chr(64+len(EXPECTED_HEADER))}1"
        result = google_sheets_service.spreadsheets().values().get(
            spreadsheetId=GOOGLE_SHEET_ID,
            range=range_to_check
        ).execute()
        values = result.get('values', [])
        if not values or values[0] != EXPECTED_HEADER:
            print("Setting/Updating Google Sheet header row...")
            body = {'values': [EXPECTED_HEADER]}
            google_sheets_service.spreadsheets().values().update(
                spreadsheetId=GOOGLE_SHEET_ID,
                range=f"{SHEET_NAME}!A1",
                valueInputOption='USER_ENTERED',
                body=body
            ).execute()
            print(f"Header row set to: {EXPECTED_HEADER}")
    except Exception as e:
        print(f"Error setting/checking Google Sheet header: {e}")

async def download_image(url):
    try:
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        return response.content
    except requests.exceptions.RequestException as e:
        print(f"Error downloading image from {url}: {e}")
        return None

async def analyze_image_with_openai(image_bytes, client_name):
    if not image_bytes:
        return None

    base64_image = base64.b64encode(image_bytes).decode('utf-8')
    
    prompt_text = (
        "Analyze the provided image of an electronic device. Your primary goal is to extract the Make, Model, Serial Number, Part Number, and MAC Address. "
        "Format your response as a single, clean JSON object with the keys: 'make', 'model', 'serial_number', 'part_number', 'mac_address'. "
        f"The client associated with this device is '{client_name}'.\n\n"
        "CRITICAL INFERENCE STEP: If the Make and Model are not clearly printed on the device, YOU MUST attempt to deduce them. "
        "Use the Organizational Unique Identifier (OUI) from the first three octets of the MAC address to identify the manufacturer (this will be the 'Make'). "
        "Then, use the Part Number, in conjunction with the inferred Make, to determine the specific 'Model'. "
        "For example, if MAC is '00:0B:82:XX:XX:XX', the OUI '000B82' indicates Grandstream Networks, Inc. If the Part Number is '962-00052-20A002', this might correspond to a 'GXP2130' model for that make.\n\n"
        "If any piece of information (Make, Model, Serial, Part Number, MAC Address) cannot be found or reliably inferred even after these steps, use the string 'N/A' as its value. "
        "Do not add any explanatory text outside of the JSON object."
    )

    try:
        response = await asyncio.to_thread(
            openai.chat.completions.create,
            model="gpt-4o",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt_text},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}
                        }
                    ]
                }
            ],
            max_tokens=450, 
            temperature=0.1 
        )
        content = response.choices[0].message.content
        print(f"OpenAI Raw Response: {content}")

        try:
            json_start = content.find('{')
            json_end = content.rfind('}') + 1
            data = {}
            if json_start != -1 and json_end != -1:
                json_str = content[json_start:json_end]
                data = json.loads(json_str)
            else: 
                print(f"Warning: Could not find JSON object in response. Content: {content}")
                return {"make": "N/A (OpenAI Format Issue)", "model": "N/A", "serial_number": "N/A", "part_number": "N/A", "mac_address": "N/A", "raw_response": content}

            extracted_info = {
                "make": data.get("make", "N/A"),
                "model": data.get("model", "N/A"),
                "serial_number": data.get("serial_number", "N/A"),
                "part_number": data.get("part_number", "N/A"),
                "mac_address": data.get("mac_address", "N/A")
            }
            return extracted_info

        except json.JSONDecodeError as e:
            print(f"Error: OpenAI did not return valid JSON. Content: {content}. Error: {e}")
            return {"make": "N/A (JSON Error)", "model": "N/A", "serial_number": "N/A", "part_number": "N/A", "mac_address": "N/A", "raw_response": content}
        except Exception as e: 
            print(f"An unexpected error occurred parsing OpenAI response: {e}")
            return {"make": "N/A (Parsing Error)", "model": "N/A", "serial_number": "N/A", "part_number": "N/A", "mac_address": "N/A", "raw_response": content}

    except openai.APIError as e: 
        print(f"OpenAI API Error: {e}")
        error_message = f"OpenAI API Error: Status {e.status_code} - {e.message}"
        return {"make": error_message, "model": "N/A", "serial_number": "N/A", "part_number": "N/A", "mac_address": "N/A"}
    except Exception as e: 
        print(f"Error calling OpenAI API: {e}")
        return {"make": f"N/A (API Call Error: {str(e)[:100]})", "model": "N/A", "serial_number": "N/A", "part_number": "N/A", "mac_address": "N/A"}


def append_to_google_sheet(data_row):
    try:
        body = {'values': [data_row]}
        result = google_sheets_service.spreadsheets().values().append(
            spreadsheetId=GOOGLE_SHEET_ID,
            range=f"{SHEET_NAME}!A1",
            valueInputOption='USER_ENTERED',
            insertDataOption='INSERT_ROWS',
            body=body
        ).execute()
        print(f"Appended to Google Sheet: {result.get('updates', {}).get('updatedCells', 0)} cells.")
        return True
    except Exception as e:
        print(f"Error appending to Google Sheet: {e}")
        return False

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    if message.channel.id == TARGET_DISCORD_CHANNEL_ID:
        if message.attachments:
            attachment = message.attachments[0]
            if attachment.content_type and attachment.content_type.startswith('image/'):
                print(f"Image attachment found: {attachment.filename} from {message.author.name}")

                client_name = message.content.strip()
                if not client_name:
                    if message.clean_content.strip(): 
                        client_name = message.clean_content.strip()
                    else:
                        await message.reply("Client name is missing. Please provide the client's name in the message text along with the image.")
                        return

                discord_user = message.author.display_name
                
                # Get the UTC timestamp from Discord
                utc_timestamp = message.created_at
                
                # Define the SAST timezone
                sast_timezone = pytz.timezone('Africa/Johannesburg') # SAST timezone
                
                # Convert UTC to SAST
                sast_timestamp = utc_timestamp.replace(tzinfo=pytz.utc).astimezone(sast_timezone)
                
                # Format it for display and Google Sheets
                formatted_sast_timestamp = sast_timestamp.strftime("%Y-%m-%d %H:%M:%S SAST")
                
                image_url = attachment.url

                processing_msg = await message.reply(f"⏳ Processing image for client: **{client_name}** (posted by {discord_user}). Please wait...")

                image_bytes = await download_image(image_url)
                if not image_bytes:
                    await processing_msg.edit(content="❌ Failed to download the image. Please try again.")
                    await message.add_reaction("⚠️")
                    return

                extracted_info = await analyze_image_with_openai(image_bytes, client_name)

                if extracted_info: 
                    make = extracted_info.get('make', 'N/A')
                    model = extracted_info.get('model', 'N/A')
                    serial = extracted_info.get('serial_number', 'N/A')
                    part_no = extracted_info.get('part_number', 'N/A')
                    mac = extracted_info.get('mac_address', 'N/A')

                    # Use the formatted SAST timestamp for the sheet_row
                    sheet_row = [formatted_sast_timestamp, discord_user, client_name, make, model, serial, part_no, mac, image_url]

                    if await asyncio.to_thread(append_to_google_sheet, sheet_row):
                        summary_reply = (
                            f"**Client:** {client_name}\n"
                            f"------------------------------------\n"
                            f"**Make:** `{make}`\n"
                            f"**Model:** `{model}`\n"
                            f"**Serial:** `{serial}`\n"
                            f"**Part No.:** `{part_no}`\n"
                            f"**MAC Address:** `{mac}`\n"
                            f"------------------------------------\n"
                            f"Image processed and data saved.\n" # Removed timestamp from here as it's in the sheet
                            f"(Time: {sast_timestamp.strftime('%Y-%m-%d %H:%M:%S %Z%z')})" # Optionally add it to the Discord reply for clarity
                        )
                        await processing_msg.edit(content=summary_reply)
                        await message.add_reaction("✅")

                        if "raw_response" in extracted_info: 
                             await message.channel.send(f"Debug note: There was an issue parsing OpenAI's structured response. Raw data snippet: ```{str(extracted_info['raw_response'])[:1000]}```")
                    else:
                        await processing_msg.edit(content="❌ Failed to save data to Google Sheet.")
                        await message.add_reaction("❌")
                else: 
                    await processing_msg.edit(content="❌ Could not extract information from the image using OpenAI. A critical error occurred during analysis.")
                    await message.add_reaction("❓")

# --- Start the Bot ---
if __name__ == "__main__":
    if not all([DISCORD_BOT_TOKEN, OPENAI_API_KEY, GOOGLE_SHEET_ID, str(TARGET_DISCORD_CHANNEL_ID).isdigit()]):
        print("Critical environment variables missing or invalid. Check .env file.")
    elif not os.path.exists(SERVICE_ACCOUNT_FILE):
        print(f"Google credentials file '{SERVICE_ACCOUNT_FILE}' missing.")
    else:
        try:
            print("Starting bot...")
            bot.run(DISCORD_BOT_TOKEN)
        except discord.errors.LoginFailure:
            print("Discord login failed. Check DISCORD_BOT_TOKEN.")
        except Exception as e:
            print(f"Bot run error: {e}")