import os
import re
from datetime import datetime
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
    ConversationHandler,
)
from dotenv import load_dotenv
import PyPDF2
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
import time

# Load token
load_dotenv()
TOKEN = os.getenv("TOKEN")

# Conversation states
WAITING_FOR_PDF, WAITING_FOR_INVOICE, WAITING_FOR_SERIAL = range(3)

# Store user data temporarily
user_data_store = {}


def number_to_words(amount):
    """Convert number to Indian rupees words"""
    if amount == 0:
        return "Zero Rupees Only"
    
    if not isinstance(amount, (int, float)) or amount < 0:
        return ""
    
    rupees = int(amount)
    paise = int(round((amount - rupees) * 100))
    
    result_words = []
    
    crores = rupees // 10000000
    rupees %= 10000000
    if crores > 0:
        result_words.append(f"{crores} Crore")
    
    lakhs = rupees // 100000
    rupees %= 100000
    if lakhs > 0:
        result_words.append(f"{lakhs} Lakh")
    
    thousands = rupees // 1000
    rupees %= 1000
    if thousands > 0:
        result_words.append(f"{thousands} Thousand")
    
    if rupees > 0:
        result_words.append(str(rupees))
    
    final_rupees_part = " ".join(result_words).strip()
    if final_rupees_part:
        final_rupees_part += " Rupees"
    
    paise_words = ""
    if paise > 0:
        paise_words = f"{paise} Paise"
    
    if final_rupees_part and paise_words:
        return f"{final_rupees_part} And {paise_words}"
    elif final_rupees_part:
        return f"{final_rupees_part} Only"
    else:
        return "Zero Rupees Only"


def format_amount(num):
    """Format number in Indian numbering system"""
    if not isinstance(num, (int, float)):
        return ""
    s = f"{num:,.2f}"
    parts = s.split('.')
    integer_part = parts[0].replace(',', '')
    if len(integer_part) > 3:
        last_three = integer_part[-3:]
        remaining = integer_part[:-3]
        result = ""
        while len(remaining) > 2:
            result = "," + remaining[-2:] + result
            remaining = remaining[:-2]
        if remaining:
            result = remaining + result
        return result + "," + last_three + "." + parts[1]
    return s


def extract_data_from_pdf(pdf_path):
    """Extract data from PDF"""
    try:
        with open(pdf_path, 'rb') as file:
            pdf_reader = PyPDF2.PdfReader(file)
            full_text = ""
            for page in pdf_reader.pages:
                full_text += page.extract_text() + " "
        
        is_hdb_doc = "HDB FINANCIAL SERVICES" in full_text
        is_idfc_bank_doc = "IDFC FIRST Bank" in full_text
        
        customer_name = ""
        manufacturer = ""
        model = ""
        asset_category = ""
        customer_address = ""
        serial_number = ""
        asset_cost = 0
        hdb_finance = False
        
        if is_hdb_doc:
            hdb_finance = True
            customer_match = re.search(r'to our Customer\s+(.+?)\s+\. Pursuant', full_text, re.I)
            customer_name = customer_match.group(1).strip() if customer_match else ""
            
            brand_match = re.search(r'Product Brand\s*:\s*([^\s]+)', full_text, re.I)
            manufacturer = brand_match.group(1).strip() if brand_match else ""
            
            model_start = full_text.find('Product Model :')
            model_end = full_text.find('Scheme Code & EMI')
            if model_start != -1 and model_end != -1 and model_end > model_start:
                model = full_text[model_start + len('Product Model :'):model_end].strip()
            
            label = 'A. Product Cost'
            idx = full_text.find(label)
            if idx != -1:
                i = idx + len(label)
                while i < len(full_text) and not full_text[i].isdigit():
                    i += 1
                num_str = ""
                while i < len(full_text) and (full_text[i].isdigit() or full_text[i] in ',.'):
                    num_str += full_text[i]
                    i += 1
                if num_str:
                    asset_cost = float(num_str.replace(',', ''))
            
            address_match = re.search(r'Customer Address\s*:\s*([\s\S]*?\d{6})', full_text, re.I)
            customer_address = address_match.group(1).strip() if address_match else ""
            
            serial_start = full_text.find('Serial Number')
            model_number_start = full_text.find('Model Number', serial_start + 1)
            if serial_start != -1 and model_number_start != -1 and model_number_start > serial_start:
                serial_number = full_text[serial_start + len('Serial Number'):model_number_start].strip()
            
            asset_category = "Electronics"
            
        elif is_idfc_bank_doc:
            customer_match = re.search(r'loan application of (.+?) has been approved for', full_text, re.I)
            customer_name = f"{customer_match.group(1).strip()} [IDFC FIRST BANK]" if customer_match else ""
            
            asset_category_match = re.search(r'Asset Category:?[ \t]*([A-Za-z\s]+?)(?=\s*(?:D\s*Model Number|Model Number|Serial Number|Asset Cost|$))', full_text, re.I)
            if asset_category_match:
                asset_category = asset_category_match.group(1).strip()
                if asset_category.endswith('D'):
                    asset_category = asset_category[:-1].strip()
            
            manufacturer = ""
            
            para = "The required formalities with the customer have been completed and hence we request you to collect the down payment and only deliver the product at the following address post device validation is completed and final DA is received."
            para_idx = full_text.find(para)
            if para_idx != -1:
                after_para = full_text[para_idx:]
                address_match = re.search(r'Customer Address[:]?', after_para, re.I)
                if address_match:
                    after_address = after_para[address_match.end():]
                    thanking_match = re.search(r'Thanking you', after_address, re.I)
                    if thanking_match:
                        customer_address = after_address[:thanking_match.start()].strip()
                    else:
                        customer_address = after_address.strip()
            
            model_match = re.search(r'Model Number:?[ \t]*([^\n\r]+?)(?=\s*(?:Scheme Name|Serial Number|Asset Category|Asset Cost|\n|\r|$))', full_text, re.I)
            if model_match:
                model = model_match.group(1).strip()
                if model.endswith('E'):
                    model = model[:-1].strip()
            
            serial_number_match = re.search(r'Serial Number:?[ \t]*([^ \t\n]+)', full_text, re.I)
            serial_number = serial_number_match.group(1).strip() if serial_number_match else ""
            
            asset_cost_match = re.search(r'Cost Of Product[\s:]*([\d,\.]+)', full_text, re.I)
            if asset_cost_match:
                cost_str = asset_cost_match.group(1).replace(',', '')
                asset_cost = float(cost_str)
        
        else:
            customer_match = re.search(r'Customer Name:?[ \t]*([A-Za-z]+(?:\s+[A-Za-z]+){0,2})', full_text, re.I)
            customer_name = customer_match.group(1).strip() if customer_match else ""
            customer_name = re.sub(r'\s+Customer$', '', customer_name).strip()
            
            manufacturer_match = re.search(r'Manufacturer:?[ \t]*([^ \t\n]+)', full_text, re.I)
            manufacturer = manufacturer_match.group(1).strip() if manufacturer_match else ""
            
            address_match = re.search(r'(?:Customer )?Address:?[ \t]*([\s\S]*?\d{6})', full_text, re.I)
            customer_address = address_match.group(1).strip() if address_match else ""
            
            asset_category_match = re.search(r'Asset Category:?[ \t]*([A-Za-z\s]+?)(?=\s*(?:Sub-Category|Variant|\bModel\b|\bSerial Number\b|\bAsset Cost\b|$))', full_text, re.I)
            asset_category = asset_category_match.group(1).strip() if asset_category_match else ""
            if asset_category.endswith('D'):
                asset_category = asset_category[:-1].strip()
            
            model_match = re.search(r'Model:?\s*([^\n\r]+?)(?=\s*Asset Category|\n|\r)', full_text, re.I)
            model = model_match.group(1).strip() if model_match else ""
            
            serial_number_match = re.search(r'Serial Number:?[ \t]*([^ \t\n]+)', full_text, re.I)
            serial_number = serial_number_match.group(1).strip() if serial_number_match else ""
            
            asset_cost_match = re.search(r'A\. Asset Cost[^\d]*(\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?)', full_text, re.I)
            if asset_cost_match:
                asset_cost = float(asset_cost_match.group(1).replace(',', ''))
        
        return {
            'customerName': customer_name,
            'customerAddress': customer_address,
            'manufacturer': manufacturer,
            'assetCategory': asset_category,
            'model': model,
            'imeiSerialNumber': serial_number,
            'date': datetime.now().strftime('%Y-%m-%d'),
            'assetCost': asset_cost,
            'hdbFinance': hdb_finance
        }
    
    except Exception as e:
        print(f"Error extracting PDF: {e}")
        raise


def calculate_tax_details(asset_cost, asset_category):
    """Calculate tax details"""
    is_air_conditioner = "AIR CONDITIONER" in asset_category.upper()
    
    if is_air_conditioner:
        rate = asset_cost / 1.28
        cgst = ((asset_cost - (asset_cost / 1.28)) / 2)
        sgst = cgst
        taxable_value = asset_cost - (sgst + cgst)
        tax_rate = 14
    else:
        rate = asset_cost / 1.18
        cgst = ((asset_cost - (asset_cost / 1.18)) / 2)
        sgst = cgst
        taxable_value = asset_cost - (sgst + cgst)
        tax_rate = 9
    
    total_tax_amount = sgst + cgst
    
    return {
        'rate': round(rate, 2),
        'cgst': round(cgst, 2),
        'sgst': round(sgst, 2),
        'taxableValue': round(taxable_value, 2),
        'taxRate': tax_rate,
        'totalTaxAmount': round(total_tax_amount, 2)
    }


def generate_invoice_pdf(pdf_path, invoice_number, output_path, serial_number=None):
    """Use Selenium to upload PDF to website and download result"""
    
    # Setup Chrome options
    options = webdriver.ChromeOptions()
    options.add_argument('--headless')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    options.add_argument('--window-size=1920,1080')
    
    # Download preferences
    download_dir = os.path.abspath("downloads")
    os.makedirs(download_dir, exist_ok=True)
    
    prefs = {
        "download.default_directory": download_dir,
        "download.prompt_for_download": False,
        "plugins.always_open_pdf_externally": True,
        "printing.print_preview_sticky_settings.appState": '{"recentDestinations":[{"id":"Save as PDF","origin":"local","account":""}],"selectedDestinationId":"Save as PDF","version":2}'
    }
    options.add_experimental_option("prefs", prefs)
    
    driver = None
    
    try:
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
        
        print("Navigating to website...")
        driver.get("https://katiyarelectronics1-three.vercel.app/")
        wait = WebDriverWait(driver, 30)
        
        # Wait for page to load
        time.sleep(3)
        
        print("Looking for file input...")
        # Find file input
        file_input = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='file']")))
        print("Found file input, uploading PDF...")
        file_input.send_keys(os.path.abspath(pdf_path))
        
        # Wait for PDF to process
        time.sleep(5)
        
        print("Looking for invoice number input...")
        # Find invoice number input - try multiple selectors
        invoice_input = None
        input_selectors = [
            "//input[contains(translate(@placeholder, 'INVOICE', 'invoice'), 'invoice')]",
            "//input[@type='text']",
            "//input[@type='number']",
            "input[type='text']",
            "input[type='number']",
            "input"
        ]
        
        for selector in input_selectors:
            try:
                if selector.startswith("//"):
                    inputs = driver.find_elements(By.XPATH, selector)
                else:
                    inputs = driver.find_elements(By.CSS_SELECTOR, selector)
                
                for inp in inputs:
                    try:
                        if inp.is_displayed() and inp.is_enabled() and inp.get_attribute('type') != 'file':
                            invoice_input = inp
                            print(f"Found input field with selector: {selector}")
                            break
                    except:
                        continue
                if invoice_input:
                    break
            except:
                continue
        
        if not invoice_input:
            raise Exception("Could not find invoice number input field")
        
        print(f"Found invoice input, entering: {invoice_number}")
        invoice_input.clear()
        invoice_input.send_keys(invoice_number)
        
        time.sleep(2)
        
        # Handle serial number if provided
        if serial_number:
            print(f"Looking for serial number input field...")
            serial_input = None
            serial_selectors = [
                "//input[contains(translate(@placeholder, 'SERIAL', 'serial'), 'serial')]",
                "//input[contains(translate(@placeholder, 'IMEI', 'imei'), 'imei')]",
                "//label[contains(translate(text(), 'SERIAL', 'serial'), 'serial')]/following::input[1]",
                "//label[contains(translate(text(), 'IMEI', 'imei'), 'imei')]/following::input[1]"
            ]
            
            for selector in serial_selectors:
                try:
                    inputs = driver.find_elements(By.XPATH, selector)
                    for inp in inputs:
                        try:
                            if inp.is_displayed() and inp.is_enabled() and inp.get_attribute('type') != 'file':
                                serial_input = inp
                                print(f"Found serial input field with selector: {selector}")
                                break
                        except:
                            continue
                    if serial_input:
                        break
                except:
                    continue
            
            if serial_input:
                print(f"Entering serial number: {serial_number}")
                serial_input.clear()
                serial_input.send_keys(serial_number)
                time.sleep(1)
            else:
                print("Warning: Could not find serial number input field, continuing without it...")
        else:
            print("No serial number provided, skipping...")
        
        time.sleep(2)
        
        print("Looking for download button...")
        # Find download button - try multiple selectors
        button_selectors = [
            "//button[contains(translate(text(), 'PRINTDOWNLOAD', 'printdownload'), 'print')]",
            "//button[contains(translate(text(), 'PRINTDOWNLOAD', 'printdownload'), 'download')]",
            "//button[contains(., 'Print')]",
            "//button[contains(., 'Download')]",
            "//input[@type='button' and contains(@value, 'Print')]",
            "//input[@type='button' and contains(@value, 'Download')]",
            "button"
        ]
        
        button = None
        for selector in button_selectors:
            try:
                if selector.startswith("//"):
                    buttons = driver.find_elements(By.XPATH, selector)
                else:
                    buttons = driver.find_elements(By.CSS_SELECTOR, selector)
                
                for btn in buttons:
                    try:
                        if btn.is_displayed() and btn.is_enabled():
                            button_text = btn.text.lower()
                            if 'print' in button_text or 'download' in button_text or selector == "button":
                                button = btn
                                print(f"Found button: '{btn.text}' with selector: {selector}")
                                break
                    except:
                        continue
                if button:
                    break
            except:
                continue
        
        if not button:
            raise Exception("Could not find download button")
        
        print("Clicking download button...")
        
        # Store the current window handle
        original_window = driver.current_window_handle
        all_windows_before = driver.window_handles
        
        driver.execute_script("arguments[0].click();", button)
        
        # Wait a moment for any new window/tab to open
        time.sleep(3)
        
        # Check if a new window/tab opened
        all_windows_after = driver.window_handles
        
        if len(all_windows_after) > len(all_windows_before):
            # New window opened, switch to it
            print("New window detected, switching...")
            for window in all_windows_after:
                if window != original_window:
                    driver.switch_to.window(window)
                    print(f"Switched to new window: {driver.current_url}")
                    break
        else:
            print("No new window, staying on current page")
        
        # Wait for the invoice page to fully load
        print("Waiting for invoice page to render...")
        time.sleep(8)
        
        print(f"Current URL: {driver.current_url}")
        print(f"Page title: {driver.title}")
        
        # Take a screenshot for debugging
        try:
            debug_screenshot = "debug_invoice_page.png"
            driver.save_screenshot(debug_screenshot)
            print(f"Debug screenshot saved: {debug_screenshot}")
        except:
            pass
        
        # Try to detect invoice content
        try:
            invoice_elements = driver.find_elements(By.XPATH, "//*[contains(text(), 'Tax Invoice') or contains(text(), 'INVOICE') or contains(text(), 'Invoice') or contains(text(), 'KATIYAR')]")
            if invoice_elements:
                print(f"Found {len(invoice_elements)} invoice-related elements")
            else:
                print("Warning: No invoice elements found")
        except Exception as e:
            print(f"Could not search for invoice elements: {e}")
        
        time.sleep(2)
        
        print("Generating PDF from current page...")
        # Use Chrome's print to PDF
        result = driver.execute_cdp_cmd("Page.printToPDF", {
            "printBackground": True,
            "landscape": False,
            "paperWidth": 8.27,  # A4 width in inches
            "paperHeight": 11.69,  # A4 height in inches
            "marginTop": 0.4,
            "marginBottom": 0.4,
            "marginLeft": 0.4,
            "marginRight": 0.4,
            "preferCSSPageSize": True,
            "displayHeaderFooter": False,
            "scale": 1.0
        })
        
        # Save the PDF
        import base64
        with open(output_path, 'wb') as f:
            f.write(base64.b64decode(result['data']))
        
        print(f"PDF saved: {output_path}")
        
        if not os.path.exists(output_path):
            raise Exception("Failed to generate PDF")
        
        return output_path
        
    except Exception as e:
        print(f"Error in generate_invoice_pdf: {str(e)}")
        if driver:
            try:
                screenshot_path = "error_screenshot.png"
                driver.save_screenshot(screenshot_path)
                print(f"Screenshot saved to {screenshot_path}")
            except:
                pass
        raise
        
    finally:
        if driver:
            driver.quit()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Welcome! Send me a PDF file.")
    return WAITING_FOR_PDF


async def handle_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    document = update.message.document
    if document.mime_type != "application/pdf":
        await update.message.reply_text("Please send a valid PDF file.")
        return WAITING_FOR_PDF
    file = await context.bot.get_file(document.file_id)
    file_path = f"temp_{update.effective_user.id}_{document.file_name}"
    await file.download_to_drive(file_path)
    user_data_store[update.effective_user.id] = {"pdf_path": file_path}
    await update.message.reply_text("PDF received! Now send me the Invoice Number.")
    return WAITING_FOR_INVOICE


async def handle_invoice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    invoice_number = update.message.text.strip()
    user_id = update.effective_user.id
    if user_id not in user_data_store:
        await update.message.reply_text("Please send a PDF first using /start")
        return ConversationHandler.END
    user_data_store[user_id]["invoice_number"] = invoice_number
    await update.message.reply_text("Invoice number received!\n\nNow send the Serial Number (or press Enter/send '-' to skip if you don't want to add one).")
    return WAITING_FOR_SERIAL


async def handle_serial(update: Update, context: ContextTypes.DEFAULT_TYPE):
    serial_number = update.message.text.strip()
    user_id = update.effective_user.id
    
    if user_id not in user_data_store:
        await update.message.reply_text("Please send a PDF first using /start")
        return ConversationHandler.END
    
    # Check if user wants to skip serial number
    if serial_number == "-" or serial_number == "" or serial_number.lower() == "skip":
        serial_number = None
        await update.message.reply_text("Skipping serial number. Processing...")
    else:
        await update.message.reply_text(f"Serial number received: {serial_number}\n\nProcessing...")
    
    pdf_path = user_data_store[user_id]["pdf_path"]
    invoice_number = user_data_store[user_id]["invoice_number"]
    
    try:
        extracted_data = extract_data_from_pdf(pdf_path)
        output_path = f"invoice_{invoice_number}_{user_id}.pdf"
        generate_invoice_pdf(pdf_path, invoice_number, output_path, serial_number)
        with open(output_path, 'rb') as pdf_file:
            await update.message.reply_document(
                document=pdf_file,
                filename=f"invoice_{invoice_number}.pdf",
                caption=f"✅ Invoice generated!\n\nCustomer: {extracted_data['customerName']}\nAmount: ₹{format_amount(extracted_data['assetCost'])}"
            )
        if os.path.exists(pdf_path):
            os.remove(pdf_path)
        if os.path.exists(output_path):
            os.remove(output_path)
        del user_data_store[user_id]
        await update.message.reply_text("Done! Send /start for another invoice.")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}\n\nTry again with /start")
        if os.path.exists(pdf_path):
            os.remove(pdf_path)
        if user_id in user_data_store:
            del user_data_store[user_id]
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Cancelled. Send /start to begin again.")
    user_id = update.effective_user.id
    if user_id in user_data_store:
        pdf_path = user_data_store[user_id].get("pdf_path")
        if pdf_path and os.path.exists(pdf_path):
            os.remove(pdf_path)
        del user_data_store[user_id]
    return ConversationHandler.END


def main():
    app = ApplicationBuilder().token(TOKEN).build()
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            WAITING_FOR_PDF: [MessageHandler(filters.Document.PDF, handle_pdf)],
            WAITING_FOR_INVOICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_invoice)],
            WAITING_FOR_SERIAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_serial)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(conv_handler)
    print("✅ Bot running...")
    app.run_polling()


if __name__ == "__main__":
    main()
