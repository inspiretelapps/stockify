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
import pytz
import pathlib # Import the pathlib module

# --- Load Environment Variables ---
# Determine the absolute path to the directory where stock_bot.py is located
current_dir = pathlib.Path(__file__).parent.resolve()
# Construct the path to the .env file within this script's directory
dotenv_path = current_dir / ".env" 

print(f"Attempting to load .env file from: {dotenv_path}") # For debugging

if dotenv_path.exists():
    load_dotenv(dotenv_path=dotenv_path)
    print(f".env file loaded successfully from {dotenv_path}")
else:
    print(f"Warning: .env file not found at {dotenv_path}. Environment variables might not be set correctly.")


# --- Load Environment Variables ---
DISCORD_BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
GOOGLE_SHEET_ID = os.getenv('GOOGLE_SHEET_ID')
MACVENDORS_API_TOKEN = os.getenv('MACVENDORS_API_TOKEN') 

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

# --- Helper Function for MAC Formatting ---
def format_mac_address(mac_string):
    if not mac_string or mac_string.lower() == "n/a":
        return "N/A"
    cleaned_mac = "".join(filter(str.isalnum, mac_string)).upper()
    if len(cleaned_mac) == 12:
        try:
            int(cleaned_mac, 16) 
            return ":".join(cleaned_mac[i:i+2] for i in range(0, 12, 2))
        except ValueError:
            print(f"Warning: MAC address '{mac_string}' (cleaned: '{cleaned_mac}') contains non-hex characters after cleaning. Returning N/A.")
            return "N/A" 
    else:
        print(f"Warning: Cleaned MAC address '{cleaned_mac}' is not 12 characters long. Original: '{mac_string}'. Returning N/A.")
        return "N/A"

# --- get_vendor_from_mac for macvendors.com ---
async def get_vendor_from_mac(mac_address_str):
    if not mac_address_str or mac_address_str.lower() == "n/a":
        return None
    if not MACVENDORS_API_TOKEN:
        print("Error: MACVENDORS_API_TOKEN not found in environment variables. Cannot perform MAC lookup.")
        return None
    
    url = f"https://api.macvendors.com/v1/lookup/{mac_address_str}"
    headers = {
        "Authorization": f"Bearer {MACVENDORS_API_TOKEN}",
        "Accept": "application/json" 
    }

    try:
        print(f"Python MAC Lookup (macvendors.com): Looking up MAC address: {mac_address_str}")
        response = await asyncio.to_thread(requests.get, url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            try:
                data = response.json() 
                if isinstance(data, dict) and "data" in data and isinstance(data["data"], dict) and "organization_name" in data["data"]:
                    vendor_name = data["data"]["organization_name"]
                    vendor_name_cleaned = vendor_name.split(" CO.")[0].split(" LTD")[0].split(" INC")[0].split(" LLC")[0].split(" GMBH")[0].strip()
                    if "YEALINK" in vendor_name_cleaned.upper():
                        vendor_name_cleaned = "Yealink"
                    print(f"Python MAC Lookup Result (macvendors.com) for {mac_address_str}: Original='{vendor_name}', Cleaned='{vendor_name_cleaned}'")
                    return vendor_name_cleaned
                elif isinstance(data, str): 
                    if "not found" not in data.lower() and "errors" not in data.lower():
                         print(f"Python MAC Lookup Result (macvendors.com) for {mac_address_str} (plain text): {data}")
                         return data 
                    else:
                         print(f"Python MAC Lookup (macvendors.com) for {mac_address_str}: No vendor found by API (plain text error): '{data}'")
                         return None
                else:
                    print(f"Python MAC Lookup (macvendors.com) for {mac_address_str}: JSON response did not contain expected 'data.organization_name'. Response: {data}")
                    return None
            except json.JSONDecodeError:
                error_text = response.text.strip()
                if "not found" in error_text.lower():
                    print(f"Python MAC Lookup (macvendors.com) for {mac_address_str}: No vendor found by API (plain text 'Not Found').")
                else:
                    print(f"Python MAC Lookup (macvendors.com) for {mac_address_str}: Response was not valid JSON despite status 200. Text: {error_text[:200]}")
                return None
            except Exception as e_parse:
                print(f"Python MAC Lookup (macvendors.com) - Error parsing response for {mac_address_str}: {e_parse}, Response: {response.text[:200]}")
                return None
        elif response.status_code == 401:
            print(f"Python MAC Lookup Error (macvendors.com) for {mac_address_str}: 401 Unauthorized. Check your MACVENDORS_API_TOKEN.")
            return None
        elif response.status_code == 404: 
            print(f"Python MAC Lookup (macvendors.com) for {mac_address_str}: Resource not found (404). MAC might be invalid or not in DB.")
            return None
        else:
            print(f"Python MAC Lookup Error (macvendors.com) for {mac_address_str}: Status {response.status_code}, Response: {response.text[:200]}")
            return None
    except requests.exceptions.Timeout:
        print(f"Python MAC Lookup Error (macvendors.com) for {mac_address_str}: Request timed out.")
        return None
    except requests.exceptions.RequestException as e:
        print(f"Python MAC Lookup Error (macvendors.com) for {mac_address_str}: {e}")
        return None
    except Exception as e_outer:
        print(f"An unexpected error occurred in get_vendor_from_mac for {mac_address_str}: {e_outer}")
        return None

async def set_sheet_header_if_needed():
    try:
        end_column_letter = chr(64 + len(EXPECTED_HEADER)) 
        range_to_check = f"{SHEET_NAME}!A1:{end_column_letter}1"
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
        else:
            print("Google Sheet header row is already correct.")
    except Exception as e:
        print(f"Error setting/checking Google Sheet header: {e}")

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
    
    # UPDATED PROMPT
    prompt_text = (
        "Analyze the provided image which may contain one or more electronic devices or their labels (e.g., on boxes). "
        "Your goal is to identify each distinct item/device shown. For EACH item, extract the following information: "
        "Make, Model, Serial Number (S/N), a generic Part Number (P/N) if available, a Dell Part Number (DP/N) if available, "
        "a Vendor Product Number (VPN) if available, and the MAC Address. MAC addresses consist of 12 hexadecimal characters; please ensure complete extraction. "
        f"The client associated with these items is '{client_name}'.\n\n"
        "Format your response as a single JSON ARRAY, where each element in the array is a JSON object representing one detected item. "
        "Each JSON object should have the keys: 'make', 'model', 'serial_number', 'part_number', 'dp_n', 'vpn', 'mac_address'.\n\n"
        "INSTRUCTIONS FOR INFERRING 'MAKE' AND 'MODEL' FOR EACH ITEM:\n"
        "1. Attempt to directly read any explicit 'Make' or 'Model' labels on the item.\n"
        "2. For 'Make': \n"
        "   a. Prioritize explicit 'Make' labels if present.\n"
        "   b. If no explicit 'Make' label, examine part numbers. For example, a 'DP/N' often indicates 'Dell'. Part numbers starting with '90NB' often indicate 'ASUS'. Look for similar manufacturer-specific P/N patterns.\n"
        "   c. If still no 'Make', and a MAC address is available, consider its OUI (e.g., '44:DD:2C' for Yealink, '00:0B:82' for Grandstream) to suggest a manufacturer.\n"
        "3. For 'Model': \n"
        "   a. Use explicit 'Model' labels first (e.g., text explicitly labeled 'Model' or specific patterns like 'W73H', 'K6502VV', 'KM5221W', 'GXP2130').\n"
        "   b. If not explicitly labeled, consider if the 'VPN', 'DP/N', or generic 'P/N' provides a model identifier, especially in context of the determined 'Make'.\n"
        "4. If 'Make' or 'Model' cannot be reasonably determined for an item after considering all these clues, use 'N/A'.\n\n"
        "If an image shows multiple distinct items, create a separate JSON object for each. "
        "Ensure all keys are present in each JSON object, using 'N/A' where appropriate. "
        "Extract MAC addresses as they appear, even without delimiters (e.g., 'AABBCCDDEEFF'). The script will format them later. "
        "Do not add any explanatory text outside of the main JSON array structure."
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
            max_tokens=1024, 
            temperature=0.1 
        )
        content = response.choices[0].message.content
        print(f"OpenAI Raw Response: {content}")

        processed_items_list = [] 

        try:
            array_start = content.find('[')
            array_end = content.rfind(']') + 1
            list_of_item_data = []
            
            if array_start != -1 and array_end != -1:
                json_array_str = content[array_start:array_end]
                parsed_json = json.loads(json_array_str) 

                if isinstance(parsed_json, list):
                    list_of_item_data = parsed_json
                elif isinstance(parsed_json, dict):
                    print("Warning: OpenAI returned a single object, expected an array. Processing as one item.")
                    list_of_item_data = [parsed_json]
                else:
                    raise TypeError(f"Expected a list or dict from OpenAI JSON, got {type(parsed_json)}")
            else:
                print(f"Warning: Could not find JSON array brackets in response. Attempting to parse as single object. Content: {content}")
                json_start_obj = content.find('{')
                json_end_obj = content.rfind('}') + 1
                if json_start_obj != -1 and json_end_obj != -1:
                    json_obj_str = content[json_start_obj:json_end_obj]
                    single_item_data = json.loads(json_obj_str)
                    list_of_item_data = [single_item_data] 
                    print("Interpreted as a single item object.")
                else:
                    raise json.JSONDecodeError("No JSON array or object found", content, 0)

            for item_data in list_of_item_data:
                item_make = item_data.get("make", "N/A")
                item_model = item_data.get("model", "N/A")
                serial_number = item_data.get("serial_number", "N/A")
                part_number = item_data.get("part_number", "N/A") 
                dp_n = item_data.get("dp_n", "N/A")            
                vpn = item_data.get("vpn", "N/A")              
                
                raw_mac_address = item_data.get("mac_address", "N/A")
                mac_address = format_mac_address(raw_mac_address)

                final_make = item_make
                final_model = item_model

                if (final_make == "N/A" or not final_make or final_make.lower() == "unknown") and \
                   (mac_address != "N/A"): 
                    print(f"Item S/N: {serial_number} - OpenAI Make is '{final_make}'. Formatted MAC address '{mac_address}' found. Attempting Python MAC lookup.")
                    vendor_from_mac = await get_vendor_from_mac(mac_address)
                    if vendor_from_mac:
                        final_make = vendor_from_mac
                        print(f"Item S/N: {serial_number} - Python MAC lookup updated Make to: '{final_make}'")
                
                if (final_make == "N/A" or not final_make or final_make.lower() == "unknown") and \
                   (dp_n != "N/A" and dp_n): # Check DP/N if Make still not found by OpenAI or MAC lookup
                    final_make = "Dell"
                    print(f"Item S/N: {serial_number} - Python fallback inferred Make 'Dell' from DP/N: {dp_n}")
                
                # Model inference logic
                if (final_model == "N/A" or not final_model or final_model.lower() == "unknown"):
                    if vpn != "N/A" and vpn:
                        final_model = vpn 
                        print(f"Item S/N: {serial_number} - Python fallback using VPN as Model: {vpn}")
                    elif final_make == "Dell" and (dp_n != "N/A" and dp_n): # If it's Dell and VPN didn't give model
                        final_model = dp_n 
                        print(f"Item S/N: {serial_number} - Python fallback using DP/N as Model for Dell device: {dp_n}")
                    # You could add more model inference logic here based on part_number if needed,
                    # especially if final_make is now known (e.g., if final_make == "ASUS" and part_number starts with "90NB...")
                
                item_info = {
                    "make": final_make if final_make and final_make.lower() != "n/a" and final_make.lower() != "unknown" else "N/A",
                    "model": final_model if final_model and final_model.lower() != "n/a" and final_model.lower() != "unknown" else "N/A",
                    "serial_number": serial_number,
                    "part_number": part_number,
                    "mac_address": mac_address 
                }
                processed_items_list.append(item_info)
            
            if not processed_items_list and content:
                 return [{"make": "N/A (Processing Issue)", "model": "N/A", "serial_number": "N/A", "part_number": "N/A", "mac_address": "N/A", "raw_response": content[:500]}]

            return processed_items_list

        except json.JSONDecodeError as e:
            print(f"Error: OpenAI did not return valid JSON. Content: {content}. Error: {e}")
            return [{"make": "N/A (JSON Error)", "model": "N/A", "serial_number": "N/A", "part_number": "N/A", "mac_address": "N/A", "raw_response": content[:500]}]
        except Exception as e: 
            print(f"An unexpected error occurred parsing OpenAI response: {e}")
            return [{"make": "N/A (Parsing Error)", "model": "N/A", "serial_number": "N/A", "part_number": "N/A", "mac_address": "N/A", "raw_response": str(e)[:500]}]

    except openai.APIError as e: 
        print(f"OpenAI API Error: {e}")
        error_message = f"OpenAI API Error: Status {e.status_code} - {e.message}"
        return [{"make": error_message, "model": "N/A", "serial_number": "N/A", "part_number": "N/A", "mac_address": "N/A"}]
    except Exception as e: 
        print(f"Error calling OpenAI API: {e}")
        return [{"make": f"N/A (API Call Error: {str(e)[:100]})", "model": "N/A", "serial_number": "N/A", "part_number": "N/A", "mac_address": "N/A"}]

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
                utc_timestamp = message.created_at
                sast_timezone = pytz.timezone('Africa/Johannesburg')
                sast_timestamp = utc_timestamp.astimezone(sast_timezone)
                formatted_sast_timestamp = sast_timestamp.strftime("%Y-%m-%d %H:%M:%S SAST")
                image_url = attachment.url

                processing_msg = await message.reply(f"⏳ Processing image for client: **{client_name}** (posted by {discord_user}). ")

                image_bytes = await download_image(image_url)
                if not image_bytes:
                    await processing_msg.edit(content="❌ Failed to download the image. Please try again.")
                    await message.add_reaction("⚠️")
                    return

                list_of_extracted_items = await analyze_image_with_openai(image_bytes, client_name)

                if list_of_extracted_items: 
                    successful_saves = 0
                    summary_parts = [f"**Client:** {client_name}"]
                    
                    for item_info in list_of_extracted_items:
                        if "raw_response" in item_info and ("Error" in item_info.get("make", "") or "Issue" in item_info.get("make", "")):
                            make_val = item_info.get('make', 'Error Processing Item') 
                            summary_parts.append(
                                f"------------------------------------\n"
                                f"⚠️ **Item Processing Issue:** `{make_val}`\n"
                                f"Raw Data Snippet: ```{str(item_info.get('raw_response','N/A'))[:200]}```"
                            )
                            continue

                        make = item_info.get('make', 'N/A')
                        model = item_info.get('model', 'N/A')
                        serial = item_info.get('serial_number', 'N/A')
                        part_no = item_info.get('part_number', 'N/A')
                        mac = item_info.get('mac_address', 'N/A') 
                        
                        sheet_row = [formatted_sast_timestamp, discord_user, client_name, make, model, serial, part_no, mac, image_url]

                        if await asyncio.to_thread(append_to_google_sheet, sheet_row):
                            successful_saves += 1
                            summary_parts.append(
                                f"------------------------------------\n"
                                f"**Make:** `{make}`\n"
                                f"**Model:** `{model}`\n"
                                f"**Serial:** `{serial}`\n"
                                f"**Part No.:** `{part_no}`\n"
                                f"**MAC Address:** `{mac}`"
                            )
                        else:
                            summary_parts.append(
                                f"------------------------------------\n"
                                f"⚠️ **Failed to save item to Google Sheet:** S/N `{serial if serial != 'N/A' else 'Unknown'}`"
                            )
                    
                    if successful_saves > 0:
                        summary_parts.append(f"------------------------------------\n{successful_saves} item(s) processed and data saved.")
                        await message.add_reaction("✅")
                    else:
                        if any("Item Processing Issue" in part for part in summary_parts) or any("Failed to save item" in part for part in summary_parts):
                             summary_parts.append(f"------------------------------------\nNo items successfully saved. Please check issues above.")
                        else:
                            summary_parts.append(f"------------------------------------\nNo valid item data extracted or saved from image.")
                        await message.add_reaction("❌")
                    
                    summary_parts.append(f"(Time: {sast_timestamp.strftime('%Y-%m-%d %H:%M:%S %Z%z')})")
                    full_summary_message = "\n".join(summary_parts)
                    
                    if len(full_summary_message) > 1990:
                        full_summary_message = full_summary_message[:1990] + "\n... (message truncated)"
                    await processing_msg.edit(content=full_summary_message)

                else: 
                    await processing_msg.edit(content="❌ Could not extract any information from the image using OpenAI. A critical error likely occurred before or during analysis.")
                    await message.add_reaction("❓")

# --- Start the Bot ---
if __name__ == "__main__":
    if not all([DISCORD_BOT_TOKEN, OPENAI_API_KEY, GOOGLE_SHEET_ID, str(TARGET_DISCORD_CHANNEL_ID).isdigit()]):
        print("Critical environment variables missing or invalid. Check .env file.")
    elif not MACVENDORS_API_TOKEN:
        print("Error: MACVENDORS_API_TOKEN not found in .env file. This is required for MAC address lookup.")
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