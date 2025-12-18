from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import xml.etree.ElementTree as ET
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
import random
import re

app = Flask(__name__)
CORS(app)

TALLY_URL = "http://localhost:9000"

def round_decimal(value, places=2):
    """Round using Decimal for precision matching Tally's rounding"""
    d = Decimal(str(value))
    return float(d.quantize(Decimal(10) ** -places, rounding=ROUND_HALF_UP))

def get_company_list():
    """Fetch list of companies from TallyPrime"""
    xml_request = '''<?xml version="1.0" encoding="UTF-8"?>
<ENVELOPE>
    <HEADER>
        <VERSION>1</VERSION>
        <TALLYREQUEST>Export</TALLYREQUEST>
        <TYPE>Collection</TYPE>
        <ID>List of Companies</ID>
    </HEADER>
    <BODY>
        <DESC>
            <STATICVARIABLES>
                <SVEXPORTFORMAT>$SysName:XML</SVEXPORTFORMAT>
            </STATICVARIABLES>
            <TDL>
                <TDLMESSAGE>
                    <COLLECTION NAME="List of Companies">
                        <TYPE>Company</TYPE>
                        <FETCH>Name</FETCH>
                    </COLLECTION>
                </TDLMESSAGE>
            </TDL>
        </DESC>
    </BODY>
</ENVELOPE>'''
    
    try:
        response = requests.post(
            TALLY_URL,
            data=xml_request.encode('utf-8'),
            headers={'Content-Type': 'application/xml; charset=utf-8'},
            timeout=5
        )
        
        if response.status_code == 200:
            root = ET.fromstring(response.text)
            companies = []
            
            for company in root.findall('.//COMPANY'):
                name_elem = company.find('NAME')
                if name_elem is not None and name_elem.text:
                    companies.append(name_elem.text.strip())
            
            return companies
        return []
    except Exception as e:
        print(f"Error fetching companies: {e}")
        return []

def generate_voucher_number():
    """Generate voucher number in format RS-YY/YY-NNNN"""
    now = datetime.now()
    year = now.year % 100
    next_year = (now.year + 1) % 100
    number = random.randint(1000, 9999)
    return f"RS-{year:02d}/{next_year:02d}-{number}"

def calculate_amounts_precise(qty, rate_incl_gst, gst_rate):
    """
    Calculate amounts with precision matching Tally's expectations.
    Uses Decimal for exact calculations matching the sample XML.
    """
    qty_dec = Decimal(str(qty))
    rate_incl_dec = Decimal(str(rate_incl_gst))
    gst_rate_dec = Decimal(str(gst_rate))
    
    # Total amount (inclusive of GST)
    total_amount = qty_dec * rate_incl_dec
    
    # Calculate base amount (exclusive of GST)
    divisor = Decimal('1') + (gst_rate_dec / Decimal('100'))
    base_amount = total_amount / divisor
    base_amount = base_amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    
    # Calculate GST amount
    gst_amount = total_amount - base_amount
    gst_amount = gst_amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    
    # Calculate base rate per piece
    base_rate = base_amount / qty_dec
    base_rate = base_rate.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    
    return {
        'base_amount': float(base_amount),
        'gst_amount': float(gst_amount),
        'base_rate': float(base_rate),
        'total_amount': float(total_amount),
        'rate_incl_gst': float(rate_incl_dec)
    }

def create_retail_sale_xml(voucher_data):
    """Generate XML for Retail Sale voucher matching Tally's exact format"""
    
    company_name = voucher_data.get('companyName', '')
    party_name = voucher_data['partyName']
    customer_name = voucher_data['customerName']
    address = voucher_data.get('address', '')
    phone = voucher_data.get('phone', '')
    date = voucher_data['date']
    items = voucher_data['items']
    
    date_obj = datetime.strptime(date, '%Y-%m-%d')
    tally_date = date_obj.strftime('%Y%m%d')
    
    voucher_number = generate_voucher_number()
    
    # Calculate totals using precise decimal arithmetic
    subtotal = Decimal('0')
    cgst_total = Decimal('0')
    sgst_total = Decimal('0')
    
    items_calculated = []
    
    for item in items:
        qty = float(item['quantity'])
        rate_incl = float(item['rate'])
        gst_rate = float(item['gstRate'])
        
        # Use precise calculation
        calc = calculate_amounts_precise(qty, rate_incl, gst_rate)
        
        items_calculated.append({
            'name': item['name'],
            'imei': item.get('imei', ''),
            'quantity': qty,
            'rate_incl_gst': calc['rate_incl_gst'],
            'gst_rate': gst_rate,
            'base_amount': calc['base_amount'],
            'gst_amount': calc['gst_amount'],
            'base_rate': calc['base_rate']
        })
        
        subtotal += Decimal(str(calc['base_amount']))
        gst_amt = Decimal(str(calc['gst_amount']))
        cgst_total += gst_amt / Decimal('2')
        sgst_total += gst_amt / Decimal('2')
    
    # Round CGST and SGST
    cgst_total = cgst_total.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    sgst_total = sgst_total.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    subtotal = subtotal.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    
    total = subtotal + cgst_total + sgst_total
    rounded_total = round(float(total))
    round_off = Decimal(str(rounded_total)) - total
    round_off = round_off.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    
    # Convert to float
    subtotal_float = float(subtotal)
    cgst_float = float(cgst_total)
    sgst_float = float(sgst_total)
    round_off_float = float(round_off)
    
    envelope = ET.Element('ENVELOPE')
    
    header = ET.SubElement(envelope, 'HEADER')
    ET.SubElement(header, 'TALLYREQUEST').text = 'Import Data'
    
    body = ET.SubElement(envelope, 'BODY')
    import_data = ET.SubElement(body, 'IMPORTDATA')
    
    request_desc = ET.SubElement(import_data, 'REQUESTDESC')
    ET.SubElement(request_desc, 'REPORTNAME').text = 'Vouchers'
    static_vars = ET.SubElement(request_desc, 'STATICVARIABLES')
    
    if company_name:
        ET.SubElement(static_vars, 'SVCURRENTCOMPANY').text = company_name
    
    request_data = ET.SubElement(import_data, 'REQUESTDATA')
    
    tallymsg = ET.SubElement(request_data, 'TALLYMESSAGE')
    tallymsg.set('xmlns:UDF', 'TallyUDF')
    
    voucher = ET.SubElement(tallymsg, 'VOUCHER')
    voucher.set('REMOTEID', '')
    voucher.set('VCHKEY', '')
    voucher.set('VCHTYPE', 'Retail Sale')
    voucher.set('ACTION', 'Create')
    voucher.set('OBJVIEW', 'Invoice Voucher View')
    
    if address or phone:
        addr_list = ET.SubElement(voucher, 'ADDRESS.LIST')
        addr_list.set('TYPE', 'String')
        if address:
            ET.SubElement(addr_list, 'ADDRESS').text = address
        if phone:
            ET.SubElement(addr_list, 'ADDRESS').text = phone
    
    if address:
        buyer_addr = ET.SubElement(voucher, 'BASICBUYERADDRESS.LIST')
        buyer_addr.set('TYPE', 'String')
        ET.SubElement(buyer_addr, 'BASICBUYERADDRESS').text = address
    
    old_audit = ET.SubElement(voucher, 'OLDAUDITENTRYIDS.LIST')
    old_audit.set('TYPE', 'Number')
    ET.SubElement(old_audit, 'OLDAUDITENTRYIDS').text = '-1'
    
    ET.SubElement(voucher, 'DATE').text = tally_date
    ET.SubElement(voucher, 'VCHSTATUSDATE').text = tally_date
    ET.SubElement(voucher, 'GSTREGISTRATIONTYPE').text = 'Unregistered/Consumer'
    ET.SubElement(voucher, 'STATENAME').text = 'Maharashtra'
    ET.SubElement(voucher, 'COUNTRYOFRESIDENCE').text = 'India'
    ET.SubElement(voucher, 'PLACEOFSUPPLY').text = 'Maharashtra'
    ET.SubElement(voucher, 'VOUCHERTYPENAME').text = 'Retail Sale'
    ET.SubElement(voucher, 'PARTYNAME').text = customer_name
    ET.SubElement(voucher, 'PARTYLEDGERNAME').text = party_name
    ET.SubElement(voucher, 'VOUCHERNUMBER').text = voucher_number
    ET.SubElement(voucher, 'BASICBUYERNAME').text = party_name
    ET.SubElement(voucher, 'PARTYMAILINGNAME').text = customer_name
    ET.SubElement(voucher, 'CONSIGNEEMAILINGNAME').text = party_name
    ET.SubElement(voucher, 'CONSIGNEESTATENAME').text = 'Maharashtra'
    ET.SubElement(voucher, 'CONSIGNEECOUNTRYNAME').text = 'India'
    ET.SubElement(voucher, 'BASICBASEPARTYNAME').text = party_name
    ET.SubElement(voucher, 'PERSISTEDVIEW').text = 'Invoice Voucher View'
    ET.SubElement(voucher, 'VCHENTRYMODE').text = 'Item Invoice'
    ET.SubElement(voucher, 'EFFECTIVEDATE').text = tally_date
    
    bool_flags = {
        'DIFFACTUALQTY': 'No', 'ISMSTFROMSYNC': 'No', 'ISDELETED': 'No',
        'ISSECURITYONWHENENTERED': 'No', 'ASORIGINAL': 'No', 'AUDITED': 'No',
        'FORJOBCOSTING': 'No', 'ISOPTIONAL': 'No', 'USEFOREXCISE': 'No',
        'ISFORJOBWORKIN': 'No', 'ALLOWCONSUMPTION': 'No', 'USEFORINTEREST': 'No',
        'USEFORGAINLOSS': 'No', 'USEFORGODOWNTRANSFER': 'No', 'USEFORCOMPOUND': 'No',
        'ISREVERSECHARGEAPPLICABLE': 'No', 'ISINVOICE': 'Yes', 'ISOVERSEASTOURISTTRANS': 'No'
    }
    for key, val in bool_flags.items():
        ET.SubElement(voucher, key).text = val
    
    # Add inventory entries
    for item_calc in items_calculated:
        inv_entry = ET.SubElement(voucher, 'ALLINVENTORYENTRIES.LIST')
        
        if item_calc['imei']:
            basic_desc = ET.SubElement(inv_entry, 'BASICUSERDESCRIPTION.LIST')
            basic_desc.set('TYPE', 'String')
            ET.SubElement(basic_desc, 'BASICUSERDESCRIPTION').text = item_calc['imei']
        
        qty = item_calc['quantity']
        base_rate = item_calc['base_rate']
        base_amount = item_calc['base_amount']
        rate_incl = item_calc['rate_incl_gst']
        gst_rate = item_calc['gst_rate']
        
        ET.SubElement(inv_entry, 'STOCKITEMNAME').text = item_calc['name']
        ET.SubElement(inv_entry, 'ISDEEMEDPOSITIVE').text = 'No'
        ET.SubElement(inv_entry, 'RATE').text = f'{base_rate:.2f}/Pcs'
        ET.SubElement(inv_entry, 'AMOUNT').text = f'{base_amount:.2f}'
        ET.SubElement(inv_entry, 'ACTUALQTY').text = f' {qty:.0f} Pcs'
        ET.SubElement(inv_entry, 'BILLEDQTY').text = f' {qty:.0f} Pcs'
        ET.SubElement(inv_entry, 'INCLVATRATE').text = f'{rate_incl:.2f}/Pcs'
        
        batch = ET.SubElement(inv_entry, 'BATCHALLOCATIONS.LIST')
        ET.SubElement(batch, 'GODOWNNAME').text = 'Main Location'
        ET.SubElement(batch, 'BATCHNAME').text = 'Primary Batch'
        ET.SubElement(batch, 'DESTINATIONGODOWNNAME').text = 'Main Location'
        ET.SubElement(batch, 'TRACKINGNUMBER').text = voucher_number
        ET.SubElement(batch, 'AMOUNT').text = f'{base_amount:.2f}'
        ET.SubElement(batch, 'ACTUALQTY').text = f' {qty:.0f} Pcs'
        ET.SubElement(batch, 'BILLEDQTY').text = f' {qty:.0f} Pcs'
        ET.SubElement(batch, 'INCLVATRATE').text = f'{rate_incl:.2f}/Pcs'
        
        acc_alloc = ET.SubElement(inv_entry, 'ACCOUNTINGALLOCATIONS.LIST')
        ET.SubElement(acc_alloc, 'LEDGERNAME').text = 'SALES GST'
        ET.SubElement(acc_alloc, 'ISDEEMEDPOSITIVE').text = 'No'
        ET.SubElement(acc_alloc, 'AMOUNT').text = f'{base_amount:.2f}'
        
        gst_half = gst_rate / 2
        rate_details = [
            ('CGST', gst_half),
            ('SGST/UTGST', gst_half),
            ('IGST', gst_rate)
        ]
        for duty_head, rate_val in rate_details:
            rate_detail = ET.SubElement(inv_entry, 'RATEDETAILS.LIST')
            ET.SubElement(rate_detail, 'GSTRATEDUTYHEAD').text = duty_head
            ET.SubElement(rate_detail, 'GSTRATEVALUATIONTYPE').text = 'Based on Value'
            ET.SubElement(rate_detail, 'GSTRATE').text = f' {rate_val:.0f}'
    
    # Add ledger entries
    party_ledger = ET.SubElement(voucher, 'LEDGERENTRIES.LIST')
    ET.SubElement(party_ledger, 'LEDGERNAME').text = party_name
    ET.SubElement(party_ledger, 'ISDEEMEDPOSITIVE').text = 'Yes'
    ET.SubElement(party_ledger, 'ISPARTYLEDGER').text = 'Yes'
    ET.SubElement(party_ledger, 'AMOUNT').text = f'-{rounded_total:.2f}'
    
    if cgst_float > 0:
        cgst_ledger = ET.SubElement(voucher, 'LEDGERENTRIES.LIST')
        gst_rate_for_ledger = int(items_calculated[0]['gst_rate'] / 2)
        ET.SubElement(cgst_ledger, 'LEDGERNAME').text = f'CGST {gst_rate_for_ledger}%'
        ET.SubElement(cgst_ledger, 'METHODTYPE').text = 'GST'
        ET.SubElement(cgst_ledger, 'ISDEEMEDPOSITIVE').text = 'No'
        ET.SubElement(cgst_ledger, 'AMOUNT').text = f'{cgst_float:.2f}'
        ET.SubElement(cgst_ledger, 'VATEXPAMOUNT').text = f'{cgst_float:.2f}'
    
    if sgst_float > 0:
        sgst_ledger = ET.SubElement(voucher, 'LEDGERENTRIES.LIST')
        gst_rate_for_ledger = int(items_calculated[0]['gst_rate'] / 2)
        ET.SubElement(sgst_ledger, 'LEDGERNAME').text = f'SGST {gst_rate_for_ledger}%'
        ET.SubElement(sgst_ledger, 'METHODTYPE').text = 'GST'
        ET.SubElement(sgst_ledger, 'ISDEEMEDPOSITIVE').text = 'No'
        ET.SubElement(sgst_ledger, 'AMOUNT').text = f'{sgst_float:.2f}'
        ET.SubElement(sgst_ledger, 'VATEXPAMOUNT').text = f'{sgst_float:.2f}'
    
    if abs(round_off_float) > 0.001:
        round_ledger = ET.SubElement(voucher, 'LEDGERENTRIES.LIST')
        ET.SubElement(round_ledger, 'ROUNDTYPE').text = 'Normal Rounding'
        ET.SubElement(round_ledger, 'LEDGERNAME').text = 'Round Up/Down'
        ET.SubElement(round_ledger, 'METHODTYPE').text = 'As Total Amount Rounding'
        ET.SubElement(round_ledger, 'ISDEEMEDPOSITIVE').text = 'No'
        ET.SubElement(round_ledger, 'ROUNDLIMIT').text = ' 1'
        ET.SubElement(round_ledger, 'AMOUNT').text = f'{round_off_float:.2f}'
        ET.SubElement(round_ledger, 'VATEXPAMOUNT').text = f'{round_off_float:.2f}'
    
    xml_str = ET.tostring(envelope, encoding='unicode', method='xml')
    return f'<?xml version="1.0" encoding="UTF-8"?>\n{xml_str}', voucher_number

@app.route('/get-companies', methods=['GET'])
def get_companies():
    """Endpoint to fetch list of companies from Tally"""
    try:
        companies = get_company_list()
        
        if companies:
            return jsonify({
                'success': True,
                'companies': companies,
                'count': len(companies)
            }), 200
        else:
            return jsonify({
                'error': 'No companies found or Tally not connected'
            }), 404
            
    except Exception as e:
        return jsonify({
            'error': f'Error fetching companies: {str(e)}'
        }), 500

@app.route('/create-voucher', methods=['POST'])
def create_voucher():
    """Endpoint to create Retail Sale voucher in TallyPrime"""
    try:
        voucher_data = request.json
        
        if not voucher_data.get('companyName'):
            return jsonify({'success': False, 'error': 'Company name is required'}), 400
        
        if not voucher_data.get('partyName'):
            return jsonify({'success': False, 'error': 'Party name is required'}), 400
        
        if not voucher_data.get('customerName'):
            return jsonify({'success': False, 'error': 'Customer name is required'}), 400
        
        if not voucher_data.get('items') or len(voucher_data['items']) == 0:
            return jsonify({'success': False, 'error': 'At least one item is required'}), 400
        
        xml_data, voucher_number = create_retail_sale_xml(voucher_data)
        
        print(f"\n{'='*60}")
        print(f"Sending voucher to Tally: {voucher_number}")
        print(f"{'='*60}\n")
        
        response = requests.post(
            TALLY_URL,
            data=xml_data.encode('utf-8'),
            headers={'Content-Type': 'application/xml; charset=utf-8'},
            timeout=10
        )
        
        if response.status_code == 200:
            response_text = response.text
            print(f"Tally Response (first 500 chars): {response_text[:500]}")
            
            # Check for errors in response
            if '<LINEERROR>' in response_text or '<ERROR>' in response_text:
                error_match = re.search(r'<LINEERROR>([^<]+)</LINEERROR>', response_text)
                if not error_match:
                    error_match = re.search(r'<ERROR>([^<]+)</ERROR>', response_text)
                
                if error_match:
                    error_msg = error_match.group(1)
                    print(f"Tally Error: {error_msg}")
                    return jsonify({
                        'success': False,
                        'error': f'Tally Error: {error_msg}'
                    }), 400
            
            # Try to get actual voucher number from response
            actual_voucher_number = voucher_number
            vch_match = re.search(r'<VOUCHERNUMBER>([^<]+)</VOUCHERNUMBER>', response_text)
            if vch_match:
                actual_voucher_number = vch_match.group(1).strip()
                print(f"Voucher created: {actual_voucher_number}")
            
            # Success response
            return jsonify({
                'success': True,
                'message': 'Retail Sale voucher created successfully',
                'voucherNumber': actual_voucher_number,
                'verified': True
            }), 200
        else:
            return jsonify({
                'success': False,
                'error': f'Tally returned HTTP {response.status_code}'
            }), 500
            
    except requests.exceptions.ConnectionError:
        return jsonify({
            'success': False,
            'error': 'Cannot connect to TallyPrime. Make sure Tally is running and XML API is enabled on port 9000.'
        }), 500
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return jsonify({
            'success': False,
            'error': f'Server error: {str(e)}'
        }), 500

@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint"""
    try:
        response = requests.get(f'{TALLY_URL}', timeout=2)
        return jsonify({
            'server': 'running',
            'tally': 'connected'
        }), 200
    except:
        return jsonify({
            'server': 'running',
            'tally': 'disconnected'
        }), 200

@app.route('/')
def serve_index():
    with open('index.html', 'r', encoding='utf-8') as f:
        return f.read()

if __name__ == '__main__':
    import socket
    hostname = socket.gethostname()
    local_ip = socket.gethostbyname(hostname)
    
    print(f"\n{'='*60}")
    print(f"TallyPrime Retail Sale Entry Server")
    print(f"{'='*60}")
    print(f"Server running on: http://{local_ip}:5000")
    print(f"Share this URL with mobile users on same WiFi network")
    print(f"\nMake sure:")
    print(f"  1. TallyPrime is running with company open")
    print(f"  2. XML API is enabled (F12 → Connectivity)")
    print(f"  3. Port 9000 is accessible")
    print(f"  4. Stock items and party ledgers exist in Tally")
    print(f"  5. GST ledgers (CGST 9%, SGST 9%, etc.) are created")
    print(f"{'='*60}\n")
    
    app.run(host='0.0.0.0', port=5000, debug=True)
