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

# --- Load Environment Variables ---
load_dotenv()
# Explicitly load .env from script's directory
current_dir = pytz.os.path.dirname(__file__) # Using pytz.os.path for consistency if pytz is already imported, or use pathlib
dotenv_path = pytz.os.path.join(current_dir, ".env")

print(f"Attempting to load .env file from: {dotenv_path}") 

if pytz.os.path.exists(dotenv_path):
    load_dotenv(dotenv_path=dotenv_path, override=True)
    print(f".env file loaded successfully from {dotenv_path} (overriding existing env vars if any)")
else:
    print(f"Warning: .env file not found at {dotenv_path}. Environment variables might not be set correctly.")


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

SHEET_NAME = 'Stockify'
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
    # This function remains the same as the previous version where it returns a list of items
    # found within the single image_bytes it's given.
    if not image_bytes:
        return None 

    base64_image = base64.b64encode(image_bytes).decode('utf-8')
    
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
        "2. For 'Make': Also consider if part number prefixes (like 'DP/N' often indicates Dell) or the MAC address OUI (e.g., '44:DD:2C' for Yealink, '00:0B:82' for Grandstream, '90NB' P/N prefix for ASUS) suggest a manufacturer. Prioritize explicit 'Make' labels if present.\n"
        "3. For 'Model': If not explicitly labeled, consider if 'VPN', 'DP/N', or 'P/N' provides a model identifier, especially in context of the 'Make'. Patterns like 'W73H', 'K6502VV', 'KM5221W', 'GXP2130' are good indicators.\n"
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
        print(f"OpenAI Raw Response for one image: {content}") # Log for individual image

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
                    print("Warning: OpenAI returned a single object for an image, expected an array. Processing as one item.")
                    list_of_item_data = [parsed_json]
                else:
                    raise TypeError(f"Expected a list or dict from OpenAI JSON for an image, got {type(parsed_json)}")
            else:
                print(f"Warning: Could not find JSON array brackets in image response. Attempting to parse as single object. Content: {content}")
                json_start_obj = content.find('{')
                json_end_obj = content.rfind('}') + 1
                if json_start_obj != -1 and json_end_obj != -1:
                    json_obj_str = content[json_start_obj:json_end_obj]
                    single_item_data = json.loads(json_obj_str)
                    list_of_item_data = [single_item_data] 
                    print("Interpreted as a single item object for an image.")
                else:
                    # If still no JSON, return a list with an error entry to be handled by the caller
                    print(f"Error: No valid JSON (array or object) found in OpenAI response for image. Content: {content}")
                    return [{"make": "N/A (OpenAI JSON Format Error)", "model": "N/A", "serial_number": "N/A", "part_number": "N/A", "mac_address": "N/A", "raw_response": content[:500]}]


            for item_data in list_of_item_data: # This loops through items found *within this single image*
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
                   (dp_n != "N/A" and dp_n):
                    final_make = "Dell"
                    print(f"Item S/N: {serial_number} - Python fallback inferred Make 'Dell' from DP/N: {dp_n}")
                
                if (final_model == "N/A" or not final_model or final_model.lower() == "unknown"):
                    if vpn != "N/A" and vpn:
                        final_model = vpn 
                        print(f"Item S/N: {serial_number} - Python fallback using VPN as Model: {vpn}")
                    elif final_make == "Dell" and (dp_n != "N/A" and dp_n):
                        final_model = dp_n 
                        print(f"Item S/N: {serial_number} - Python fallback using DP/N as Model for Dell device: {dp_n}")
                
                item_info = {
                    "make": final_make if final_make and final_make.lower() != "n/a" and final_make.lower() != "unknown" else "N/A",
                    "model": final_model if final_model and final_model.lower() != "n/a" and final_model.lower() != "unknown" else "N/A",
                    "serial_number": serial_number,
                    "part_number": part_number,
                    "mac_address": mac_address 
                }
                processed_items_list.append(item_info)
            
            if not processed_items_list and content and not list_of_item_data: # If content was there but parsing list_of_item_data failed to yield items
                 return [{"make": "N/A (No Items Parsed)", "model": "N/A", "serial_number": "N/A", "part_number": "N/A", "mac_address": "N/A", "raw_response": content[:500]}]

            return processed_items_list # List of items from this single image

        except json.JSONDecodeError as e:
            print(f"Error: OpenAI did not return valid JSON for image. Content: {content}. Error: {e}")
            return [{"make": "N/A (JSON Error for image)", "model": "N/A", "serial_number": "N/A", "part_number": "N/A", "mac_address": "N/A", "raw_response": content[:500]}]
        except Exception as e: 
            print(f"An unexpected error occurred parsing OpenAI response for image: {e}")
            return [{"make": "N/A (Parsing Error for image)", "model": "N/A", "serial_number": "N/A", "part_number": "N/A", "mac_address": "N/A", "raw_response": str(e)[:500]}]

    except openai.APIError as e: 
        print(f"OpenAI API Error for image: {e}")
        error_message = f"OpenAI API Error: Status {e.status_code} - {e.message}"
        return [{"make": error_message, "model": "N/A", "serial_number": "N/A", "part_number": "N/A", "mac_address": "N/A"}]
    except Exception as e: 
        print(f"Error calling OpenAI API for image: {e}")
        return [{"make": f"N/A (API Call Error for image: {str(e)[:100]})", "model": "N/A", "serial_number": "N/A", "part_number": "N/A", "mac_address": "N/A"}]

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

# --- Discord Bot Event Handler for Messages (UPDATED for multiple attachments) ---
@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    if message.channel.id == TARGET_DISCORD_CHANNEL_ID:
        if message.attachments: # Check if there are any attachments
            client_name = message.content.strip()
            if not client_name:
                if message.clean_content.strip():
                    client_name = message.clean_content.strip()
                else:
                    await message.reply("Client name is missing. Please provide the client's name in the message text along with the image(s).")
                    return

            discord_user = message.author.display_name
            utc_timestamp = message.created_at
            sast_timezone = pytz.timezone('Africa/Johannesburg')
            sast_timestamp = utc_timestamp.astimezone(sast_timezone)
            formatted_sast_timestamp = sast_timestamp.strftime("%Y-%m-%d %H:%M:%S SAST")

            all_processed_items_from_message = [] # Stores dicts for EACH item from ALL images
            any_image_found_and_processed = False
            overall_processing_had_errors = False # Tracks if any step had an error

            num_attachments = len(message.attachments)
            processing_msg_text = f"⏳ Processing {num_attachments} attachment(s) for client: **{client_name}** (posted by {discord_user}). Please wait..."
            if num_attachments == 1:
                 processing_msg_text = f"⏳ Processing 1 attachment for client: **{client_name}** (posted by {discord_user}). Please wait..."
            
            processing_msg = await message.reply(processing_msg_text)
            
            # Loop through each attachment in the Discord message
            for attachment_index, attachment_obj in enumerate(message.attachments):
                current_image_url = attachment_obj.url # URL of the current specific image attachment

                if attachment_obj.content_type and attachment_obj.content_type.startswith('image/'):
                    print(f"Processing Discord attachment {attachment_index + 1}/{num_attachments}: {attachment_obj.filename}")
                    any_image_found_and_processed = True
                    
                    image_bytes = await download_image(current_image_url)
                    if not image_bytes:
                        # Create an error entry for this specific image download failure
                        all_processed_items_from_message.append({
                            "make": f"Error downloading image: {attachment_obj.filename}", 
                            "is_error_entry": True, "raw_response": "Download failed"
                        })
                        overall_processing_had_errors = True
                        continue # Move to the next attachment

                    # list_of_items_in_current_image is a list of dicts, one for each item found in *this one image*
                    list_of_items_in_current_image = await analyze_image_with_openai(image_bytes, client_name)

                    if list_of_items_in_current_image:
                        for item_info_from_openai in list_of_items_in_current_image:
                            # Add common message-level info and this specific image's URL to each item
                            item_info_with_context = {
                                **item_info_from_openai,
                                "timestamp": formatted_sast_timestamp,
                                "discord_user": discord_user,
                                "client_name": client_name,
                                "image_url": current_image_url # URL of THIS image attachment
                            }
                            # Check if this item_info_from_openai itself indicates an error from parsing/OpenAI
                            if "raw_response" in item_info_from_openai and \
                               ("Error" in item_info_from_openai.get("make", "") or \
                                "Issue" in item_info_from_openai.get("make", "") or \
                                "N/A (API Call Error" in item_info_from_openai.get("make","")): # Check for specific error markers
                                item_info_with_context["is_error_entry"] = True
                                overall_processing_had_errors = True
                            
                            all_processed_items_from_message.append(item_info_with_context)
                    else: # analyze_image_with_openai returned None or an empty list for this image
                        all_processed_items_from_message.append({
                            "make": f"No data extracted from image: {attachment_obj.filename}",
                            "is_error_entry": True, "raw_response": "analyze_image_with_openai returned no items",
                            "image_url": current_image_url
                        })
                        overall_processing_had_errors = True
                else:
                    print(f"Attachment {attachment_index + 1}/{num_attachments}: {attachment_obj.filename} is not an image. Skipping.")
            
            # --- Consolidate results and reply to Discord ---
            if not any_image_found_and_processed and message.attachments:
                await processing_msg.edit(content="No valid image attachments found in the message to process.")
                return

            if not all_processed_items_from_message and any_image_found_and_processed:
                 await processing_msg.edit(content="❌ No information could be extracted or processed from the provided image(s).")
                 await message.add_reaction("❓")
                 return

            successful_saves = 0
            summary_parts = [f"**Client:** {client_name} (from message by {discord_user})"]

            for final_item_data in all_processed_items_from_message:
                if final_item_data.get("is_error_entry"):
                    error_make = final_item_data.get('make', 'Processing Error')
                    summary_parts.append(f"------------------------------------\n⚠️ **Item Error:** `{error_make}`")
                    if "raw_response" in final_item_data:
                        summary_parts.append(f"   Details: ```{str(final_item_data.get('raw_response','N/A'))[:100]}```")
                    continue

                make = final_item_data.get('make', 'N/A')
                model = final_item_data.get('model', 'N/A')
                serial = final_item_data.get('serial_number', 'N/A')
                part_no = final_item_data.get('part_number', 'N/A')
                mac = final_item_data.get('mac_address', 'N/A')
                # Get the specific image URL associated with this item for the sheet
                img_url_for_sheet = final_item_data.get('image_url', "N/A") 

                sheet_row = [
                    final_item_data.get('timestamp', formatted_sast_timestamp), 
                    final_item_data.get('discord_user', discord_user), 
                    final_item_data.get('client_name', client_name), 
                    make, model, serial, part_no, mac, 
                    img_url_for_sheet # Use the specific image URL for this item
                ]

                if await asyncio.to_thread(append_to_google_sheet, sheet_row):
                    successful_saves += 1
                    summary_parts.append(
                        f"------------------------------------\n"
                        f"(From Image: {os.path.basename(img_url_for_sheet)})\n" # Indicate source image
                        f"**Make:** `{make}`\n"
                        f"**Model:** `{model}`\n"
                        f"**Serial:** `{serial}`\n"
                        f"**Part No.:** `{part_no}`\n"
                        f"**MAC Address:** `{mac}`"
                    )
                else:
                    overall_processing_had_errors = True # Mark that a sheet save failed
                    summary_parts.append(
                        f"------------------------------------\n"
                        f"⚠️ **Failed to save item to Google Sheet:** S/N `{serial if serial != 'N/A' else 'Unknown'}` (from {os.path.basename(img_url_for_sheet)})"
                    )
            
            # Final status reporting
            if successful_saves > 0 and not overall_processing_had_errors:
                summary_parts.append(f"------------------------------------\n✅ All {successful_saves} item(s) processed and data saved successfully.")
                await message.add_reaction("✅")
            elif successful_saves > 0 and overall_processing_had_errors:
                summary_parts.append(f"------------------------------------\n⚠️ {successful_saves} item(s) saved, but some errors occurred. Please review details above.")
                await message.add_reaction("PARTIAL_SUCCESS_EMOJI") # Replace with actual emoji like ⚠️ or create custom one
            elif overall_processing_had_errors: # No saves, only errors
                summary_parts.append(f"------------------------------------\n❌ No items were successfully saved. Errors occurred during processing.")
                await message.add_reaction("❌")
            elif not any_image_found_and_processed: # Should be caught earlier, but as a fallback
                summary_parts = ["No image attachments found to process."] # Reset summary
            else: # No items processed from any image, no specific errors flagged (e.g. all images empty of data)
                summary_parts.append(f"------------------------------------\nℹ️ No valid item data found in the provided image(s) to save.")
                await message.add_reaction("ℹ️")


            summary_parts.append(f"(Time: {sast_timestamp.strftime('%Y-%m-%d %H:%M:%S %Z%z')})")
            full_summary_message = "\n".join(summary_parts)
            
            if len(full_summary_message) > 1990:
                full_summary_message = full_summary_message[:1990] + "\n... (message truncated)"
            
            await processing_msg.edit(content=full_summary_message)

# --- Start the Bot ---
if __name__ == "__main__":
    # ... (startup checks remain the same)
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