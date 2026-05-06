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
from xhtml2pdf import pisa
from io import BytesIO

# Load token
load_dotenv()
TOKEN = os.getenv("TOKEN")

# Conversation states
WAITING_FOR_PDF, WAITING_FOR_INVOICE = range(2)

# Store user data temporarily
user_data_store = {}


def number_to_words(amount):
    """Convert number to Indian rupees words"""
    if amount == 0:
        return "Zero Rupees Only"
    
    if not isinstance(amount, (int, float)) or amount < 0:
        return ""
    
    # Split into rupees and paise
    rupees = int(amount)
    paise = int(round((amount - rupees) * 100))
    
    result_words = []
    
    # Crores
    crores = rupees // 10000000
    rupees %= 10000000
    if crores > 0:
        result_words.append(f"{crores} Crore")
    
    # Lakhs
    lakhs = rupees // 100000
    rupees %= 100000
    if lakhs > 0:
        result_words.append(f"{lakhs} Lakh")
    
    # Thousands
    thousands = rupees // 1000
    rupees %= 1000
    if thousands > 0:
        result_words.append(f"{thousands} Thousand")
    
    # Remaining rupees
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
    # Convert to Indian format
    parts = s.split('.')
    integer_part = parts[0].replace(',', '')
    if len(integer_part) > 3:
        last_three = integer_part[-3:]
        remaining = integer_part[:-3]
        # Add commas every 2 digits for Indian format
        result = ""
        while len(remaining) > 2:
            result = "," + remaining[-2:] + result
            remaining = remaining[:-2]
        if remaining:
            result = remaining + result
        return result + "," + last_three + "." + parts[1]
    return s


def extract_data_from_pdf(pdf_path):
    """Extract data from PDF using the React code logic"""
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
            
            # Customer name
            customer_match = re.search(r'to our Customer\s+(.+?)\s+\. Pursuant', full_text, re.I)
            customer_name = customer_match.group(1).strip() if customer_match else ""
            
            # Manufacturer
            brand_match = re.search(r'Product Brand\s*:\s*([^\s]+)', full_text, re.I)
            manufacturer = brand_match.group(1).strip() if brand_match else ""
            
            # Model
            model_start = full_text.find('Product Model :')
            model_end = full_text.find('Scheme Code & EMI')
            if model_start != -1 and model_end != -1 and model_end > model_start:
                model = full_text[model_start + len('Product Model :'):model_end].strip()
            
            # Asset cost
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
            
            # Address
            address_match = re.search(r'Customer Address\s*:\s*([\s\S]*?\d{6})', full_text, re.I)
            customer_address = address_match.group(1).strip() if address_match else ""
            
            # Serial number
            serial_start = full_text.find('Serial Number')
            model_number_start = full_text.find('Model Number', serial_start + 1)
            if serial_start != -1 and model_number_start != -1 and model_number_start > serial_start:
                serial_number = full_text[serial_start + len('Serial Number'):model_number_start].strip()
            
            asset_category = "Electronics"
            
        elif is_idfc_bank_doc:
            # Customer name
            customer_match = re.search(r'loan application of (.+?) has been approved for', full_text, re.I)
            customer_name = f"{customer_match.group(1).strip()} [IDFC FIRST BANK]" if customer_match else ""
            
            # Asset category
            asset_category_match = re.search(r'Asset Category:?[ \t]*([A-Za-z\s]+?)(?=\s*(?:D\s*Model Number|Model Number|Serial Number|Asset Cost|$))', full_text, re.I)
            if asset_category_match:
                asset_category = asset_category_match.group(1).strip()
                if asset_category.endswith('D'):
                    asset_category = asset_category[:-1].strip()
            
            manufacturer = ""
            
            # Address
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
            
            # Model
            model_match = re.search(r'Model Number:?[ \t]*([^\n\r]+?)(?=\s*(?:Scheme Name|Serial Number|Asset Category|Asset Cost|\n|\r|$))', full_text, re.I)
            if model_match:
                model = model_match.group(1).strip()
                if model.endswith('E'):
                    model = model[:-1].strip()
            
            # Serial number
            serial_number_match = re.search(r'Serial Number:?[ \t]*([^ \t\n]+)', full_text, re.I)
            serial_number = serial_number_match.group(1).strip() if serial_number_match else ""
            
            # Asset cost
            asset_cost_match = re.search(r'Cost Of Product[\s:]*([\d,\.]+)', full_text, re.I)
            if asset_cost_match:
                cost_str = asset_cost_match.group(1).replace(',', '')
                asset_cost = float(cost_str)
        
        else:
            # Generic PDF
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
    """Calculate tax details based on asset category"""
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



def generate_bill_html(extracted_data, invoice_number):
    """Generate the exact HTML matching the invoice format"""
    tax_details = calculate_tax_details(extracted_data['assetCost'], extracted_data['assetCategory'])
    amount_in_words = number_to_words(extracted_data['assetCost'])
    tax_amount_in_words = number_to_words(tax_details['totalTaxAmount'])
    serial_to_display = extracted_data['imeiSerialNumber'] or ''
    
    current_date = datetime.now().strftime('%d %b %Y')
    
    hdb_finance_row = ''
    if extracted_data['hdbFinance']:
        hdb_finance_row = '''
            <tr>
                <td colspan="7" style="text-align:center; font-weight:bold; color:#1a237e; font-size:10px; padding:8px;">
                    FINANCE BY HDBFS
                </td>
            </tr>
        '''
    
    serial_row = f'<b>Serial Number:</b> {serial_to_display}<br>' if serial_to_display else ''
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>Tax Invoice</title>
        <style>
            @page {{
                size: A4;
                margin: 8mm;
            }}
            * {{
                margin: 0;
                padding: 0;
                box-sizing: border-box;
            }}
            body {{
                font-family: Arial, sans-serif;
                font-size: 8px;
                line-height: 1.3;
                padding: 3mm;
            }}
            .invoice-title {{
                text-align: center;
                font-size: 18px;
                font-weight: bold;
                margin-bottom: 5px;
            }}
            table {{
                width: 100%;
                border-collapse: collapse;
                margin: 0;
            }}
            td {{
                border: 1px solid #000;
                padding: 3px 5px;
                vertical-align: top;
                font-size: 8px;
            }}
            .no-border {{
                border: none;
            }}
            .text-center {{
                text-align: center;
            }}
            .text-right {{
                text-align: right;
            }}
            .font-bold {{
                font-weight: bold;
            }}
            .bg-light {{
                background-color: #f0f0f0;
            }}
            .company-cell {{
                font-size: 8px;
                line-height: 1.4;
            }}
            .separator {{
                border-top: 1px solid #000;
                margin: 3px 0;
                padding: 0;
            }}
        </style>
    </head>
    <body>
        <div class="invoice-title">Tax Invoice</div>
        
        <!-- Header Table -->
        <table style="margin-bottom: 0;">
            <tr>
                <td rowspan="6" style="width: 40%; font-size: 8px; padding: 5px;">
                    <div class="font-bold">KATIYAR ELECTRONICS</div>
                    <div>H.I.G.J-33 VISHWABANK BARRA</div>
                    <div>KARRAHI</div>
                    <div>KANPUR NAGAR</div>
                    <div>GSTIN/UIN: 09AMTPK9751D1ZH</div>
                    <div>State Name: Uttar Pradesh, Code: 09</div>
                    <div>E-Mail: katiyars952@gmail.com</div>
                    <div class="separator"></div>
                    <div class="font-bold">Consignee (Ship to)</div>
                    <div>{extracted_data['customerName']}</div>
                    <div>{extracted_data['customerAddress']}</div>
                    <div class="separator"></div>
                    <div class="font-bold">Buyer (Bill to)</div>
                    <div>{extracted_data['customerName']}</div>
                    <div>{extracted_data['customerAddress']}</div>
                </td>
                <td style="width: 30%; text-align: center; font-weight: bold; padding: 5px;">
                    Invoice No.<br><br>{invoice_number}
                </td>
                <td style="width: 30%; text-align: center; font-weight: bold; padding: 5px;">
                    Dated<br><br>{current_date}
                </td>
            </tr>
            <tr>
                <td style="text-align: center; font-weight: bold; padding: 5px;">Delivery Note<br><br></td>
                <td style="padding: 5px;"></td>
            </tr>
            <tr>
                <td style="text-align: center; font-weight: bold; padding: 5px;">Buyer's Order No.<br><br></td>
                <td style="text-align: center; font-weight: bold; padding: 5px;">Dated<br><br></td>
            </tr>
            <tr>
                <td style="text-align: center; font-weight: bold; padding: 5px;">Dispatch Doc No.<br><br></td>
                <td style="text-align: center; font-weight: bold; padding: 5px;">Delivery Note Date<br><br></td>
            </tr>
            <tr>
                <td style="text-align: center; font-weight: bold; padding: 5px;">Dispatched through<br><br></td>
                <td style="text-align: center; font-weight: bold; padding: 5px;">Destination<br><br></td>
            </tr>
            <tr>
                <td colspan="2" style="padding: 5px;"></td>
            </tr>
        </table>
        
        <!-- Items Table -->
        <table style="margin-top: 0;">
            <tr style="background-color: #f0f0f0;">
                <td style="width: 3%; text-align: center; font-weight: bold; padding: 3px;">Sl</td>
                <td style="width: 37%; text-align: center; font-weight: bold; padding: 3px;">Description of Goods</td>
                <td style="width: 10%; text-align: center; font-weight: bold; padding: 3px;">HSN/SAC</td>
                <td style="width: 10%; text-align: center; font-weight: bold; padding: 3px;">Quantity</td>
                <td style="width: 15%; text-align: center; font-weight: bold; padding: 3px;">Rate</td>
                <td style="width: 5%; text-align: center; font-weight: bold; padding: 3px;">per</td>
                <td style="width: 20%; text-align: center; font-weight: bold; padding: 3px;">Amount</td>
            </tr>
            <tr>
                <td style="text-align: center; padding: 3px;">1</td>
                <td style="padding: 5px; height: 380px;">
                    <div style="font-weight: bold; margin-bottom: 8px;">{extracted_data['manufacturer']} {extracted_data['assetCategory']}</div>
                    <div style="margin-bottom: 3px;"><b>Model No:</b> {extracted_data['model']}</div>
                    {f'<div style="margin-bottom: 3px;"><b>Serial Number:</b> {serial_to_display}</div>' if serial_to_display else ''}
                    <div style="margin-top: 10px;">
                        <div style="display: table; width: 100%;">
                            <div style="display: table-row;">
                                <div style="display: table-cell; width: 50%;"><b>CGST</b></div>
                                <div style="display: table-cell; width: 50%; text-align: right;">{format_amount(tax_details['cgst'])}</div>
                            </div>
                            <div style="display: table-row;">
                                <div style="display: table-cell; width: 50%;"><b>SGST</b></div>
                                <div style="display: table-cell; width: 50%; text-align: right;">{format_amount(tax_details['sgst'])}</div>
                            </div>
                        </div>
                    </div>
                </td>
                <td style="text-align: center; padding: 3px;"></td>
                <td style="text-align: center; padding: 3px;">1 PCS</td>
                <td style="text-align: center; padding: 3px;">{format_amount(tax_details['rate'])}</td>
                <td style="text-align: center; padding: 3px;">PCS</td>
                <td style="text-align: center; padding: 3px;">{format_amount(tax_details['rate'])}</td>
            </tr>
            <tr>
                <td colspan="6" style="text-align: right; font-weight: bold; padding: 3px;">Total</td>
                <td style="text-align: center; font-weight: bold; padding: 3px;">₹ {format_amount(extracted_data['assetCost'])}</td>
            </tr>
        </table>
        
        <!-- Amount in Words -->
        <table style="margin-top: 0;">
            <tr>
                <td style="padding: 5px;">
                    <div style="font-weight: bold;">Amount Chargeable (in words)</div>
                    <div style="font-weight: bold;">INR {amount_in_words}</div>
                </td>
            </tr>
        </table>
        
        <!-- Tax Table -->
        <table style="margin-top: 0;">
            <tr style="background-color: #f0f0f0;">
                <td rowspan="2" style="width: 12%; text-align: center; font-weight: bold; padding: 3px;">HSN/SAC</td>
                <td rowspan="2" style="width: 18%; text-align: center; font-weight: bold; padding: 3px;">Taxable Value</td>
                <td colspan="2" style="text-align: center; font-weight: bold; padding: 3px;">Central Tax</td>
                <td colspan="2" style="text-align: center; font-weight: bold; padding: 3px;">State Tax</td>
                <td rowspan="2" style="width: 18%; text-align: center; font-weight: bold; padding: 3px;">Total Tax Amount</td>
            </tr>
            <tr style="background-color: #f0f0f0;">
                <td style="width: 10%; text-align: center; font-weight: bold; padding: 3px;">Rate</td>
                <td style="width: 16%; text-align: center; font-weight: bold; padding: 3px;">Amount</td>
                <td style="width: 10%; text-align: center; font-weight: bold; padding: 3px;">Rate</td>
                <td style="width: 16%; text-align: center; font-weight: bold; padding: 3px;">Amount</td>
            </tr>
            <tr>
                <td style="text-align: center; padding: 3px;"></td>
                <td style="text-align: center; padding: 3px;">{format_amount(tax_details['taxableValue'])}</td>
                <td style="text-align: center; padding: 3px;">{tax_details['taxRate']}%</td>
                <td style="text-align: center; padding: 3px;">{format_amount(tax_details['cgst'])}</td>
                <td style="text-align: center; padding: 3px;">{tax_details['taxRate']}%</td>
                <td style="text-align: center; padding: 3px;">{format_amount(tax_details['sgst'])}</td>
                <td style="text-align: center; padding: 3px;">{format_amount(tax_details['totalTaxAmount'])}</td>
            </tr>
            <tr>
                <td style="text-align: center; font-weight: bold; padding: 3px;">Total</td>
                <td style="text-align: center; font-weight: bold; padding: 3px;">{format_amount(tax_details['taxableValue'])}</td>
                <td style="padding: 3px;"></td>
                <td style="text-align: center; font-weight: bold; padding: 3px;">{format_amount(tax_details['cgst'])}</td>
                <td style="padding: 3px;"></td>
                <td style="text-align: center; font-weight: bold; padding: 3px;">{format_amount(tax_details['sgst'])}</td>
                <td style="text-align: center; font-weight: bold; padding: 3px;">{format_amount(tax_details['totalTaxAmount'])}</td>
            </tr>
            <tr>
                <td colspan="7" style="text-align: center; font-weight: bold; padding: 5px;">
                    Tax Amount (in words): INR {tax_amount_in_words}
                </td>
            </tr>
            {hdb_finance_row}
        </table>
        
        <!-- Footer Table -->
        <table style="margin-top: 3px;">
            <tr>
                <td style="width: 50%; padding: 5px;">
                    <div style="font-weight: bold; margin-bottom: 3px;">Declaration</div>
                    <div>We declare that this invoice shows the actual price of the goods described and that all particulars are true and correct.</div>
                </td>
                <td style="width: 25%; padding: 5px;">
                    <div style="font-weight: bold; margin-bottom: 8px;">Pre Authenticated by</div>
                    <div style="margin-top: 20px;">Authorised Signatory</div>
                    <div>Name:</div>
                    <div>Designation:</div>
                </td>
                <td style="width: 25%; text-align: center; padding: 5px;">
                    <div style="font-weight: bold; margin-bottom: 8px;">for KATIYAR ELECTRONICS</div>
                    <div style="margin-top: 20px;">Authorised Signatory</div>
                    <div>Name:</div>
                    <div>Designation:</div>
                </td>
            </tr>
        </table>
        
        <div style="text-align: center; font-size: 8px; margin-top: 8px;">
            <div style="font-weight: bold;">SUBJECT TO KANPUR JURISDICTION</div>
            <div>This is a Computer Generated Invoice</div>
        </div>
    </body>
    </html>
    """
    return html


def generate_invoice_pdf(extracted_data, invoice_number, output_path):
    """Generate PDF from HTML using xhtml2pdf"""
    html_content = generate_bill_html(extracted_data, invoice_number)
    
    with open(output_path, "wb") as pdf_file:
        pisa_status = pisa.CreatePDF(html_content, dest=pdf_file)
    
    if pisa_status.err:
        raise Exception("Error generating PDF")
    
    print(f"Invoice PDF generated: {output_path}")


# Telegram bot handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print(f"Received /start from {update.effective_user.username}")
    await update.message.reply_text(
        "Welcome to Invoice Generator Bot!\n\n"
        "Send me a PDF file to get started."
    )
    return WAITING_FOR_PDF


async def handle_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print(f"Received document from {update.effective_user.username}")
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
    
    pdf_path = user_data_store[user_id]["pdf_path"]
    
    await update.message.reply_text("Processing... Extracting data and generating invoice...")
    
    try:
        # Extract data from PDF
        extracted_data = extract_data_from_pdf(pdf_path)
        
        # Generate invoice PDF
        output_path = f"invoice_{invoice_number}_{user_id}.pdf"
        generate_invoice_pdf(extracted_data, invoice_number, output_path)
        
        # Send the generated PDF back
        with open(output_path, 'rb') as pdf_file:
            await update.message.reply_document(
                document=pdf_file,
                filename=f"invoice_{invoice_number}.pdf",
                caption=f"✅ Invoice generated successfully!\n\n"
                        f"Customer: {extracted_data['customerName']}\n"
                        f"Amount: ₹{format_amount(extracted_data['assetCost'])}"
            )
        
        # Cleanup
        if os.path.exists(pdf_path):
            os.remove(pdf_path)
        if os.path.exists(output_path):
            os.remove(output_path)
        
        del user_data_store[user_id]
        
        await update.message.reply_text("Done! Send /start to process another invoice.")
        
    except Exception as e:
        await update.message.reply_text(f"❌ Error processing invoice: {str(e)}\n\nPlease try again with /start")
        if os.path.exists(pdf_path):
            os.remove(pdf_path)
        if user_id in user_data_store:
            del user_data_store[user_id]
    
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Operation cancelled. Send /start to begin again.")
    user_id = update.effective_user.id
    if user_id in user_data_store:
        pdf_path = user_data_store[user_id].get("pdf_path")
        if pdf_path and os.path.exists(pdf_path):
            os.remove(pdf_path)
        del user_data_store[user_id]
    return ConversationHandler.END


# Main app
def main():
    app = ApplicationBuilder().token(TOKEN).build()
    
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            WAITING_FOR_PDF: [MessageHandler(filters.Document.PDF, handle_pdf)],
            WAITING_FOR_INVOICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_invoice)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    
    app.add_handler(conv_handler)
    
    print("✅ Invoice Generator Bot is running...")
    print(f"Token: {TOKEN[:10]}...")
    app.run_polling()


if __name__ == "__main__":
    main()
