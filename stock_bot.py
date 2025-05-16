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
        return None # Return None if no image bytes

    base64_image = base64.b64encode(image_bytes).decode('utf-8')
    
    prompt_text = (
        "Analyze the provided image which may contain one or more electronic devices or their labels (e.g., on boxes). "
        "Your goal is to identify each distinct item/device shown. For EACH item, extract the following information: "
        "Make, Model, Serial Number (S/N), a generic Part Number (P/N) if available, a Dell Part Number (DP/N) if available, "
        "a Vendor Product Number (VPN) if available, and the MAC Address. "
        f"The client associated with these items is '{client_name}'.\n\n"
        "Format your response as a single JSON ARRAY, where each element in the array is a JSON object representing one detected item. "
        "Each JSON object should have the keys: 'make', 'model', 'serial_number', 'part_number', 'dp_n', 'vpn', 'mac_address'.\n\n"
        "CRITICAL INFERENCE STEP FOR MAKE AND MODEL (for each item):\n"
        "1. If 'Make' and 'Model' are clearly printed on the item body or label, use those values directly for that item.\n"
        "2. If Make/Model are not directly printed for an item, examine its available part numbers or product numbers:\n"
        "   - If a 'DP/N' (Dell Part Number) is found for an item, its 'Make' is definitively 'Dell'. Use the 'DP/N' or an associated 'VPN' to determine its 'Model'.\n"
        "   - If a 'VPN' (Vendor Product Number) is found for an item, this often directly identifies its 'Model' or a product family. Infer its 'Make' if not obvious, possibly using MAC OUI or other clues.\n"
        "   - A generic 'P/N' or 'Part Number' should be used with other clues to help infer Make/Model for that item.\n"
        "3. Use the Organizational Unique Identifier (OUI) from the MAC address (if available for an item) to help confirm or identify its manufacturer ('Make').\n"
        "   For example, an item with MAC '00:0B:82:XX:XX:XX' (OUI '000B82') is likely from Grandstream Networks. An item with VPN 'KM5221WBKB-INT' and DP/N '016PD3' has Make 'Dell' and Model likely 'KM5221W'.\n\n"
        "If an image clearly shows multiple distinct items (e.g., two separate boxes with different serial numbers), create a separate JSON object for each in the array. "
        "If an item is only partially visible or some details are obscured, extract what you can and use 'N/A' for missing fields for that item. "
        "If any piece of information cannot be found or reliably inferred for an item, use the string 'N/A' as its value for that specific key in its JSON object. "
        "Ensure all requested keys are present in each JSON object within the array. "
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
            max_tokens=1024, # Increased for potentially multiple items and more complex JSON
            temperature=0.1 
        )
        content = response.choices[0].message.content
        print(f"OpenAI Raw Response: {content}")

        processed_items_list = [] # To store successfully processed item dicts

        try:
            # The response should be a JSON array string.
            # Find the start and end of the array.
            array_start = content.find('[')
            array_end = content.rfind(']') + 1
            
            if array_start != -1 and array_end != -1:
                json_array_str = content[array_start:array_end]
                list_of_item_data = json.loads(json_array_str)

                if not isinstance(list_of_item_data, list):
                    print(f"Warning: OpenAI did not return a list as expected. Content: {content}")
                    # Handle as a single item if it's a dict, or error out
                    if isinstance(list_of_item_data, dict):
                        list_of_item_data = [list_of_item_data] # Treat as a list with one item
                    else:
                        # This will be caught by the outer exception handler if parsing fails
                        raise TypeError("Expected a list of items from OpenAI but received other type.")
            else:
                print(f"Warning: Could not find JSON array in response. Content: {content}")
                # Try to parse as a single object if no array brackets are found, for backward compatibility or error cases
                json_start_obj = content.find('{')
                json_end_obj = content.rfind('}') + 1
                if json_start_obj != -1 and json_end_obj != -1:
                    json_obj_str = content[json_start_obj:json_end_obj]
                    single_item_data = json.loads(json_obj_str)
                    list_of_item_data = [single_item_data] # Treat as a list with one item
                    print("Interpreted as a single item object.")
                else:
                    # This will be caught by the outer exception handler if parsing fails
                    raise json.JSONDecodeError("No JSON array or object found", content, 0)


            for item_data in list_of_item_data:
                raw_extracted_make = item_data.get("make", "N/A")
                raw_extracted_model = item_data.get("model", "N/A")
                serial_number = item_data.get("serial_number", "N/A")
                part_number = item_data.get("part_number", "N/A")
                dp_n = item_data.get("dp_n", "N/A")
                vpn = item_data.get("vpn", "N/A")
                mac_address = item_data.get("mac_address", "N/A")

                final_make = raw_extracted_make
                final_model = raw_extracted_model
                
                if (final_make == "N/A" or not final_make) and (dp_n != "N/A" and dp_n):
                    final_make = "Dell"
                    print(f"Item (S/N: {serial_number}): Inferred Make 'Dell' from DP/N: {dp_n}")
                
                if (final_model == "N/A" or not final_model):
                    if vpn != "N/A" and vpn:
                        final_model = vpn 
                        print(f"Item (S/N: {serial_number}): Using VPN as Model: {vpn}")
                    elif final_make == "Dell" and (dp_n != "N/A" and dp_n):
                        final_model = dp_n 
                        print(f"Item (S/N: {serial_number}): Using DP/N as Model for Dell device: {dp_n}")

                item_info = {
                    "make": final_make if final_make and final_make != "N/A" else "N/A",
                    "model": final_model if final_model and final_model != "N/A" else "N/A",
                    "serial_number": serial_number,
                    "part_number": part_number,
                    "mac_address": mac_address
                }
                processed_items_list.append(item_info)
            
            if not processed_items_list and content: # If list is empty but we got content
                 return [{"make": "N/A (Parsing Issue)", "model": "N/A", "serial_number": "N/A", "part_number": "N/A", "mac_address": "N/A", "raw_response": content[:500]}]


            return processed_items_list

        except json.JSONDecodeError as e:
            print(f"Error: OpenAI did not return valid JSON array. Content: {content}. Error: {e}")
            return [{"make": "N/A (JSON Array Error)", "model": "N/A", "serial_number": "N/A", "part_number": "N/A", "mac_address": "N/A", "raw_response": content[:500]}] # Return list with error dict
        except Exception as e: 
            print(f"An unexpected error occurred parsing OpenAI response: {e}")
            return [{"make": "N/A (Parsing Error)", "model": "N/A", "serial_number": "N/A", "part_number": "N/A", "mac_address": "N/A", "raw_response": str(e)[:500]}]

    except openai.APIError as e: 
        print(f"OpenAI API Error: {e}")
        error_message = f"OpenAI API Error: Status {e.status_code} - {e.message}"
        return [{"make": error_message, "model": "N/A", "serial_number": "N/A", "part_number": "N/A", "mac_address": "N/A"}] # Return list with error dict
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
        # print(f"Appended to Google Sheet: {result.get('updates', {}).get('updatedCells', 0)} cells.")
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

                processing_msg = await message.reply(f"⏳ Processing image for client: **{client_name}** (posted by {discord_user}). This may take a moment for multiple items...")

                image_bytes = await download_image(image_url)
                if not image_bytes:
                    await processing_msg.edit(content="❌ Failed to download the image. Please try again.")
                    await message.add_reaction("⚠️")
                    return

                # analyze_image_with_openai now returns a list of item dictionaries or None/error list
                list_of_extracted_items = await analyze_image_with_openai(image_bytes, client_name)

                if list_of_extracted_items: 
                    successful_saves = 0
                    summary_parts = [f"**Client:** {client_name}"]
                    
                    for item_info in list_of_extracted_items:
                        # Check for raw_response key which indicates an error or parsing issue for an item
                        if "raw_response" in item_info and ("Error" in item_info.get("make", "") or "Issue" in item_info.get("make", "")):
                            make = item_info.get('make', 'Error Processing Item')
                            summary_parts.append(
                                f"------------------------------------\n"
                                f"⚠️ **Item Processing Issue:** `{make}`\n"
                                f"Raw Data Snippet: ```{str(item_info.get('raw_response','N/A'))[:200]}```"
                            )
                            continue # Skip appending this item to sheet if it's an error entry

                        make = item_info.get('make', 'N/A')
                        model = item_info.get('model', 'N/A')
                        serial = item_info.get('serial_number', 'N/A')
                        part_no = item_info.get('part_number', 'N/A')
                        mac = item_info.get('mac_address', 'N/A')
                        
                        # Ensure a serial number is present to create a distinct entry, or handle items without serials if needed.
                        # For now, we assume each distinct item object from OpenAI is worth a row.
                        # If serial is N/A for an item, it will still be logged.

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
                    else: # No items were successfully saved, but some might have been attempted.
                        if any("Item Processing Issue" in part for part in summary_parts) or any("Failed to save item" in part for part in summary_parts):
                             summary_parts.append(f"------------------------------------\nNo items successfully saved. Please check issues above.")
                        else: # This case means list_of_extracted_items might have been empty or only contained errors not caught above.
                            summary_parts.append(f"------------------------------------\nNo valid item data extracted or saved.")
                        await message.add_reaction("❌")
                    
                    summary_parts.append(f"(Time: {sast_timestamp.strftime('%Y-%m-%d %H:%M:%S %Z%z')})")
                    full_summary_message = "\n".join(summary_parts)
                    
                    # Discord message length limit is 2000 characters. Truncate if necessary.
                    if len(full_summary_message) > 1990:
                        full_summary_message = full_summary_message[:1990] + "\n... (message truncated)"
                    await processing_msg.edit(content=full_summary_message)

                else: # list_of_extracted_items is None or empty from the start (critical API failure)
                    await processing_msg.edit(content="❌ Could not extract any information from the image. A critical error likely occurred during analysis.")
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