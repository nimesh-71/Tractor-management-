# -*- coding: utf-8 -*-
from flask import Flask, render_template, request, make_response, jsonify, redirect, url_for, flash, send_from_directory
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime, timedelta
import io
import os
import re
import sys
import unicodedata
import random
import json
from urllib.parse import quote, urlparse
import logging
import traceback
from werkzeug.utils import secure_filename

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "tractor-secret-key-2026")

# ==================== CONFIGURATION ====================

# Render PostgreSQL Internal Connection String
# Get this from: Render Dashboard -> PostgreSQL -> Your Database -> Connections -> Internal Connection String
DATABASE_URL = "postgresql://agriculture_user:KSHdZQQWea1X6C2DomBqWTzKBYAXFzFM@dpg-d93818mh2hms73ce41ag-a.oregon-postgres.render.com:5432/agriculture"

logger.info(f"✅ Using DATABASE_URL for connection")

# UPI Configuration
UPI_ID = os.environ.get("UPI_ID", "nimeshab@ybl")
UPI_NAME = os.environ.get("UPI_NAME", "Nimesh AB")
UPI_MERCHANT = os.environ.get("UPI_MERCHANT", "Nimesh Agritech")

# Upload Configuration
UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'pdf'}
MAX_CONTENT_LENGTH = 16 * 1024 * 1024

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = MAX_CONTENT_LENGTH

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Store payment confirmations
payment_confirmations = {}

# ==================== DATABASE FUNCTIONS ====================

def get_db_connection():
    """Get database connection with proper SSL configuration"""
    try:
        # Use the DATABASE_URL with sslmode=require
        conn = psycopg2.connect(
            DATABASE_URL,
            sslmode='require',
            connect_timeout=30,
            keepalives=1,
            keepalives_idle=5,
            keepalives_interval=2,
            keepalives_count=2
        )
        conn.set_client_encoding('UTF8')
        logger.info("✅ Database connection successful")
        return conn
    except Exception as e:
        logger.error(f"❌ Database error (require): {e}")
        # Try with sslmode=verify-full as fallback
        try:
            conn = psycopg2.connect(
                DATABASE_URL,
                sslmode='verify-full',
                connect_timeout=30
            )
            conn.set_client_encoding('UTF8')
            logger.info("✅ Database connection successful (verify-full)")
            return conn
        except Exception as e2:
            logger.error(f"❌ Database error (verify-full): {e2}")
            # Try without SSL as last resort
            try:
                # Remove sslmode from URL
                base_url = DATABASE_URL.split('?')[0]
                conn = psycopg2.connect(
                    base_url,
                    sslmode='disable',
                    connect_timeout=30
                )
                conn.set_client_encoding('UTF8')
                logger.info("✅ Database connection successful (SSL disabled)")
                return conn
            except Exception as e3:
                logger.error(f"❌ All connection attempts failed: {e3}")
                return None

def get_db():
    """Alias for get_db_connection"""
    return get_db_connection()

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# ==================== HELPER FUNCTIONS ====================

def clean_text(text):
    if text is None:
        return ""
    if isinstance(text, bytes):
        text = text.decode('utf-8')
    return str(text).strip()

def fix_hindi_text(text):
    if not text:
        return ""
    if isinstance(text, bytes):
        text = text.decode('utf-8')
    text = str(text)
    text = unicodedata.normalize('NFC', text)
    return text

def safe_filename(name):
    safe_name = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode('ascii')
    safe_name = re.sub(r'[^a-zA-Z0-9_-]', '_', safe_name)
    safe_name = re.sub(r'_+', '_', safe_name)
    safe_name = safe_name.strip('_')
    if not safe_name:
        safe_name = "Report"
    return safe_name

def format_phone(phone):
    if phone is None:
        return '-'
    try:
        if isinstance(phone, (int, float)):
            if phone == int(phone):
                return str(int(phone))
            return str(phone)
        phone_str = str(phone).strip()
        if phone_str == '' or phone_str.lower() == 'null' or phone_str == 'None':
            return '-'
        if phone_str.endswith('.0'):
            phone_str = phone_str[:-2]
        return phone_str
    except:
        return str(phone) if phone else '-'

def round_amount(amount):
    if amount <= 0:
        return 0
    return round(amount)

def get_reason_label(reason_value):
    reason_map = {
        'plowing': '🌾 बखरनी',
        'harrowing': '🚜 प्लाउ',
        'sowing': '🌱 बुवाई',
        'Threshar': '💊 थ्रेशिंग',
        'transport': '🚛 परिवहन',
        'other': '📋 अन्य'
    }
    return reason_map.get(reason_value, reason_value)

def calculate_duration(start_time, stop_time):
    try:
        if hasattr(start_time, 'hour'):
            start_hour, start_minute = start_time.hour, start_time.minute
            stop_hour, stop_minute = stop_time.hour, stop_time.minute
        else:
            start_hour, start_minute = map(int, str(start_time).split(':')[:2])
            stop_hour, stop_minute = map(int, str(stop_time).split(':')[:2])
        
        duration_minutes = (stop_hour * 60 + stop_minute) - (start_hour * 60 + start_minute)
        hours = duration_minutes // 60
        minutes = duration_minutes % 60
        return f"{int(hours)}h {int(minutes)}m"
    except:
        return "0h 0m"

# ==================== PAYMENT FUNCTIONS ====================

def get_payment_history(farmer_name):
    conn = get_db_connection()
    if not conn:
        return []
    
    cur = conn.cursor()
    try:
        cur.execute("SET client_encoding = 'UTF8'")
        cur.execute("""
            SELECT 
                pt.payment_date,
                dt.s_date as record_date,
                pt.amount,
                pt.method,
                pt.transaction_id,
                pt.notes,
                dt.iname as farmer_name
            FROM payment_transactions pt
            JOIN daily_tractor dt ON pt.sl_no = dt.sl_no
            WHERE dt.iname ILIKE %s
            ORDER BY pt.payment_date DESC
        """, (farmer_name,))
        payments = cur.fetchall()
        return payments
    except Exception as e:
        logger.error(f"Error fetching payments: {e}")
        return []
    finally:
        cur.close()
        conn.close()

# ==================== STATISTICS FUNCTIONS ====================

def get_diesel_stats():
    conn = get_db()
    if not conn:
        return {'total_entries': 0, 'total_amount': 0, 'total_liters': 0}
    
    try:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT 
                COUNT(*) as total_entries,
                COALESCE(SUM(amount), 0) as total_amount,
                COALESCE(SUM(total_liter), 0) as total_liters
            FROM diesel_purchase
        ''')
        result = cursor.fetchone()
        cursor.close()
        conn.close()
        
        return {
            'total_entries': result[0] or 0,
            'total_amount': float(result[1] or 0),
            'total_liters': float(result[2] or 0)
        }
    except Exception as e:
        logger.error(f"Error getting diesel stats: {e}")
        return {'total_entries': 0, 'total_amount': 0, 'total_liters': 0}

def get_tractor_stats():
    conn = get_db()
    if not conn:
        return {'total_entries': 0, 'total_amount': 0, 'balance_amount': 0, 'unpaid_count': 0}
    
    try:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT 
                COUNT(*) as total_entries,
                COALESCE(SUM(amount), 0) as total_amount,
                COALESCE(SUM(balance_amount), 0) as balance_amount
            FROM daily_tractor
        ''')
        result = cursor.fetchone()
        
        cursor.execute("SELECT COUNT(*) FROM daily_tractor WHERE paid = false AND balance_amount > 0")
        unpaid_count = cursor.fetchone()[0] or 0
        
        cursor.close()
        conn.close()
        
        return {
            'total_entries': result[0] or 0,
            'total_amount': float(result[1] or 0),
            'balance_amount': float(result[2] or 0),
            'unpaid_count': unpaid_count
        }
    except Exception as e:
        logger.error(f"Error getting tractor stats: {e}")
        return {'total_entries': 0, 'total_amount': 0, 'balance_amount': 0, 'unpaid_count': 0}

def get_operation_stats():
    conn = get_db()
    if not conn:
        return {'total_entries': 0, 'total_amount': 0, 'total_crops': 0}
    
    try:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT 
                COUNT(*) as total_entries,
                COALESCE(SUM(rate * EXTRACT(EPOCH FROM (stop_time - start_time)) / 3600), 0) as total_amount
            FROM tractor_operations
        ''')
        result = cursor.fetchone()
        
        cursor.execute('SELECT COUNT(DISTINCT crop) FROM tractor_operations')
        crops_result = cursor.fetchone()
        
        cursor.close()
        conn.close()
        
        return {
            'total_entries': result[0] or 0,
            'total_amount': float(result[1] or 0),
            'total_crops': crops_result[0] or 0
        }
    except Exception as e:
        logger.error(f"Error getting operation stats: {e}")
        return {'total_entries': 0, 'total_amount': 0, 'total_crops': 0}

# ==================== DIESEL FUNCTIONS ====================

def get_all_purchases():
    conn = get_db_connection()
    if not conn:
        logger.error("❌ No database connection")
        return []
    
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        query = "SELECT * FROM diesel_purchase ORDER BY purchase_date DESC, sl_no DESC"
        
        logger.info(f"🔍 Executing: {query}")
        cursor.execute(query)
        result = cursor.fetchall()
        
        cursor.close()
        conn.close()
        
        logger.info(f"✅ Found {len(result)} records in database")
        return result
        
    except Exception as e:
        logger.error(f"❌ Error fetching purchases: {e}")
        logger.error(traceback.format_exc())
        return []

def get_summary():
    conn = get_db_connection()
    if not conn:
        return {}
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        
        query = """
            SELECT 
                COUNT(*) as total_entries,
                COALESCE(SUM(amount), 0) as total_amount,
                COALESCE(SUM(total_liter), 0) as total_liters,
                COALESCE(ROUND(AVG(rate), 2), 0) as avg_rate,
                COUNT(bill_image_path) as bills_uploaded
            FROM diesel_purchase
        """
        
        cursor.execute(query)
        result = cursor.fetchone()
        cursor.close()
        conn.close()
        
        logger.info(f"📊 Summary: {result}")
        return result
    except Exception as e:
        logger.error(f"❌ Error getting summary: {e}")
        return {}

def add_purchase(purchase_date, amount, rate, product, bill_image_path=None):
    conn = get_db_connection()
    if not conn:
        return False
    try:
        cursor = conn.cursor()
        query = """
            INSERT INTO diesel_purchase 
            (purchase_date, amount, rate, product, bill_image_path)
            VALUES (%s, %s, %s, %s, %s)
        """
        cursor.execute(query, (purchase_date, amount, rate, product, bill_image_path))
        conn.commit()
        cursor.close()
        conn.close()
        logger.info(f"✅ Purchase added: {product} - ₹{amount}")
        return True
    except Exception as e:
        logger.error(f"❌ Error adding purchase: {e}")
        if conn:
            conn.rollback()
        return False

def delete_purchase_by_id(sl_no):
    conn = get_db_connection()
    if not conn:
        return False
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT bill_image_path FROM diesel_purchase WHERE sl_no = %s", (sl_no,))
        result = cursor.fetchone()
        if result and result[0]:
            try:
                if os.path.exists(result[0]):
                    os.remove(result[0])
            except:
                pass
        cursor.execute("DELETE FROM diesel_purchase WHERE sl_no = %s", (sl_no,))
        conn.commit()
        cursor.close()
        conn.close()
        logger.info(f"✅ Purchase {sl_no} deleted")
        return True
    except Exception as e:
        logger.error(f"❌ Error deleting purchase: {e}")
        conn.rollback()
        return False

def get_purchase_by_id(sl_no):
    conn = get_db_connection()
    if not conn:
        return None
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT * FROM diesel_purchase WHERE sl_no = %s", (sl_no,))
        result = cursor.fetchone()
        cursor.close()
        conn.close()
        return result
    except Exception as e:
        logger.error(f"❌ Error fetching purchase: {e}")
        return None

def update_purchase(sl_no, purchase_date, amount, rate, product, bill_image_path=None):
    conn = get_db_connection()
    if not conn:
        return False
    try:
        cursor = conn.cursor()
        if bill_image_path:
            cursor.execute("SELECT bill_image_path FROM diesel_purchase WHERE sl_no = %s", (sl_no,))
            old_image = cursor.fetchone()
            if old_image and old_image[0] and os.path.exists(old_image[0]):
                try:
                    os.remove(old_image[0])
                except:
                    pass
            query = """
                UPDATE diesel_purchase 
                SET purchase_date = %s, amount = %s, rate = %s, product = %s, bill_image_path = %s
                WHERE sl_no = %s
            """
            cursor.execute(query, (purchase_date, amount, rate, product, bill_image_path, sl_no))
        else:
            query = """
                UPDATE diesel_purchase 
                SET purchase_date = %s, amount = %s, rate = %s, product = %s
                WHERE sl_no = %s
            """
            cursor.execute(query, (purchase_date, amount, rate, product, sl_no))
        conn.commit()
        cursor.close()
        conn.close()
        logger.info(f"✅ Purchase {sl_no} updated")
        return True
    except Exception as e:
        logger.error(f"❌ Error updating purchase: {e}")
        conn.rollback()
        return False

# ==================== DAILY TRACTOR FUNCTIONS ====================

def get_daily_tractor_entries(show_full=True, farmer_name=None):
    conn = get_db()
    if not conn:
        logger.error("No database connection")
        return []
    
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        
        if farmer_name:
            if show_full:
                cursor.execute("""
                    SELECT * FROM daily_tractor 
                    WHERE iname ILIKE %s 
                    ORDER BY s_date DESC, start_time DESC
                """, (farmer_name,))
            else:
                cursor.execute("""
                    SELECT * FROM daily_tractor 
                    WHERE iname ILIKE %s AND paid = false AND balance_amount > 0
                    ORDER BY s_date DESC, start_time DESC
                """, (farmer_name,))
        else:
            if show_full:
                cursor.execute("SELECT * FROM daily_tractor ORDER BY s_date DESC, start_time DESC")
            else:
                cursor.execute("""
                    SELECT * FROM daily_tractor 
                    WHERE paid = false AND balance_amount > 0
                    ORDER BY s_date DESC, start_time DESC
                """)
        
        entries = cursor.fetchall()
        cursor.close()
        conn.close()
        
        result = []
        for entry in entries:
            entry_dict = {
                'sl_no': entry['sl_no'],
                's_date': entry['s_date'],
                'iname': entry['iname'],
                'phone': entry.get('phone', ''),
                'start_time': entry['start_time'],
                'stop_time': entry['stop_time'],
                'rate': float(entry['rate']) if entry['rate'] else 0,
                'advance_amount': float(entry.get('advance_amount', 0)),
                'reason': entry['reason'],
                'reason_label': get_reason_label(entry['reason']),
                'paid': entry.get('paid', False),
                'remaining_balance': float(entry.get('remaining_balance', 0)),
                'duration': calculate_duration(entry['start_time'], entry['stop_time']),
                'amount': float(entry.get('amount', 0)),
                'balance_amount': float(entry.get('balance_amount', 0))
            }
            result.append(entry_dict)
        
        return result
    except Exception as e:
        logger.error(f"Error fetching daily tractor entries: {e}")
        return []

def get_farmer_names():
    conn = get_db_connection()
    if not conn:
        return []
    
    cur = conn.cursor()
    try:
        cur.execute("SET client_encoding = 'UTF8'")
        cur.execute("SELECT DISTINCT iname FROM daily_tractor WHERE iname IS NOT NULL ORDER BY iname;")
        farmer_names = []
        for name in cur.fetchall():
            clean_name = clean_text(name[0])
            farmer_names.append(clean_name)
        return farmer_names
    except Exception as e:
        logger.error(f"Error getting farmer names: {e}")
        return []
    finally:
        cur.close()
        conn.close()

def get_farmer_totals(farmer_name):
    conn = get_db_connection()
    if not conn:
        return 0.0, 0.0, 0.0
    
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT COALESCE(SUM(amount), 0), COALESCE(SUM(advance_amount), 0), 
                   COALESCE(SUM(balance_amount), 0)
            FROM daily_tractor WHERE iname ILIKE %s;
        """, (farmer_name,))
        totals = cur.fetchone()
        if totals:
            return float(totals[0] or 0), float(totals[1] or 0), float(totals[2] or 0)
        return 0.0, 0.0, 0.0
    except Exception as e:
        logger.error(f"Error getting farmer totals: {e}")
        return 0.0, 0.0, 0.0
    finally:
        cur.close()
        conn.close()

# ==================== TRACTOR OPERATION FUNCTIONS ====================

def get_tractor_operation_entries():
    conn = get_db()
    if not conn:
        logger.error("No database connection")
        return []
    
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute('SELECT * FROM tractor_operations ORDER BY sdate DESC, start_time DESC')
        entries = cursor.fetchall()
        cursor.close()
        conn.close()
        
        logger.info(f"✅ Found {len(entries)} tractor operation entries")
        
        result = []
        for entry in entries:
            entry_dict = {
                'id': entry['id'],
                'sdate': entry['sdate'],
                'name_batayidar': entry['name_batayidar'],
                'crop': entry['crop'],
                'start_time': entry['start_time'],
                'stop_time': entry['stop_time'],
                'rate': float(entry['rate']) if entry['rate'] else 0,
                'reason': entry['reason'],
                'reason_label': get_reason_label(entry['reason'])
            }
            result.append(entry_dict)
        
        return result
    except Exception as e:
        logger.error(f"Error fetching tractor operation entries: {e}")
        logger.error(traceback.format_exc())
        return []

# ==================== HTML GENERATION ====================

def generate_tractor_report_html(farmer_name, rows, total_amount, advance_amount, balance_amount, unpaid_balance, show_full):
    report_type = "FULL STATEMENT" if show_full else "UNPAID RECORDS"
    report_id = f"TR{datetime.now().strftime('%Y%m%d%H%M%S')}"
    current_date = datetime.now().strftime('%d %B %Y at %I:%M %p')
    
    payments = get_payment_history(farmer_name)
    total_payments = len(payments)
    total_paid_amount = sum(float(p[2] or 0) for p in payments)
    rounded_total = round_amount(total_amount)
    calculated_balance = total_amount - advance_amount
    
    html = f'''<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Daily Tractor Report - {farmer_name}</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ 
            font-family: 'Arial', 'Segoe UI', 'Nirmala UI', sans-serif; 
            padding: 30px; 
            background: white;
            color: #2c3e50;
            max-width: 1200px;
            margin: 0 auto;
        }}
        .header {{
            text-align: center;
            border-bottom: 3px solid #2c3e50;
            padding-bottom: 15px;
            margin-bottom: 20px;
        }}
        .header h1 {{
            color: #2c3e50;
            margin: 0;
            font-size: 28px;
            font-weight: bold;
        }}
        .header .subtitle {{
            color: #7f8c8d;
            font-size: 14px;
            margin-top: 5px;
        }}
        .report-info {{
            background: #e8f4f8;
            padding: 15px 20px;
            border-radius: 8px;
            margin: 15px 0;
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
        }}
        .report-info .label {{
            color: #7f8c8d;
            font-size: 14px;
        }}
        .report-info .value {{
            font-weight: bold;
            font-size: 16px;
        }}
        .report-info .farmer-name {{
            color: #e74c3c;
            font-weight: bold;
            font-size: 22px;
        }}
        .upi-section {{
            background: #f8f9fa;
            border: 1px solid #dee2e6;
            border-radius: 8px;
            padding: 15px 20px;
            margin: 15px 0;
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
        }}
        .upi-section .upi-label {{
            color: #7f8c8d;
            font-size: 12px;
            text-transform: uppercase;
        }}
        .upi-section .upi-value {{
            font-weight: bold;
            font-size: 16px;
        }}
        .upi-section .upi-id {{
            color: #6c5ce7;
            font-size: 20px;
            font-weight: bold;
        }}
        .summary-grid {{
            display: grid;
            grid-template-columns: repeat(5, 1fr);
            gap: 15px;
            margin: 20px 0;
        }}
        .summary-card {{
            background: #f8f9fa;
            padding: 15px;
            border-radius: 8px;
            text-align: center;
            border: 1px solid #dee2e6;
        }}
        .summary-card .label {{
            color: #7f8c8d;
            font-size: 12px;
            text-transform: uppercase;
        }}
        .summary-card .value {{
            font-size: 20px;
            font-weight: bold;
            margin-top: 5px;
        }}
        .summary-card .value.positive {{ color: #27ae60; }}
        .summary-card .value.negative {{ color: #e74c3c; }}
        .summary-card .value.primary {{ color: #3498db; }}
        .summary-card .value.balance {{ 
            color: #e74c3c; 
            font-size: 24px;
            font-weight: 900;
        }}
        .summary-card .label.balance-label {{
            color: #e74c3c;
            font-weight: 900;
            font-size: 14px;
            text-transform: uppercase;
        }}
        
        table {{
            width: 100%;
            border-collapse: collapse;
            margin-top: 20px;
            font-size: 12px;
        }}
        th {{
            background: #34495e;
            color: white;
            padding: 10px 8px;
            text-align: center;
            font-weight: 600;
            font-size: 12px;
        }}
        td {{
            padding: 8px;
            border-bottom: 1px solid #ecf0f1;
            text-align: center;
        }}
        tr:nth-child(even) {{ background: #f8f9fa; }}
        .status-paid {{ color: #27ae60; font-weight: bold; }}
        .status-unpaid {{ color: #e74c3c; font-weight: bold; }}
        
        .payment-history {{
            margin-top: 30px;
        }}
        .payment-history h3 {{
            color: #2c3e50;
            margin-bottom: 10px;
            border-bottom: 2px solid #ecf0f1;
            padding-bottom: 8px;
            font-size: 18px;
        }}
        .payment-summary {{
            background: #e8f4f8;
            padding: 10px 15px;
            border-radius: 8px;
            margin-top: 15px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
        }}
        .footer {{
            text-align: center;
            margin-top: 30px;
            padding-top: 15px;
            border-top: 2px solid #ecf0f1;
            color: #95a5a6;
            font-size: 12px;
        }}
        .no-records {{
            text-align: center;
            padding: 40px;
            color: #95a5a6;
        }}
        .no-records h3 {{
            font-size: 18px;
            margin-bottom: 10px;
        }}
        .badge {{
            display: inline-block;
            padding: 2px 10px;
            border-radius: 12px;
            font-size: 11px;
            font-weight: bold;
        }}
        .badge-upi {{
            background: #6c5ce7;
            color: white;
        }}
        .badge-cash {{
            background: #27ae60;
            color: white;
        }}
        .badge-auto {{
            background: #f39c12;
            color: white;
        }}
        .advance-note {{
            font-size: 11px;
            color: #7f8c8d;
            font-style: italic;
            margin-top: 5px;
            text-align: center;
        }}
        .phone-cell {{
            font-family: monospace;
            font-weight: bold;
        }}
        @media print {{
            body {{ padding: 15px; }}
            .no-print {{ display: none !important; }}
            .summary-card {{ background: #f8f9fa !important; }}
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>DAILY TRACTOR REPORT</h1>
        <div class="subtitle">Daily Tractor Management System</div>
    </div>
    
    <div class="report-info">
        <div>
            <span class="label">Report Date:</span>
            <span class="value">{current_date}</span>
        </div>
        <div>
            <span class="label">Farmer:</span>
            <span class="farmer-name">{farmer_name}</span>
        </div>
        <div>
            <span class="label">Report Type:</span>
            <span class="value">{report_type}</span>
        </div>
    </div>
    
    <div class="upi-section">
        <div>
            <div class="upi-label">Pay via UPI</div>
            <div class="upi-id">{UPI_ID}</div>
        </div>
        <div>
            <div class="upi-label">Payee</div>
            <div class="upi-value">{UPI_NAME}</div>
        </div>
        <div>
            <div class="upi-label">Amount</div>
            <div class="upi-value">₹{calculated_balance:,.2f}</div>
        </div>
        <div>
            <div class="upi-label">Original</div>
            <div class="upi-value">₹{total_amount:,.2f} (Rounded to ₹{rounded_total:,.2f})</div>
        </div>
    </div>
    
    <div class="summary-grid">
        <div class="summary-card">
            <div class="label">Total Records</div>
            <div class="value primary">{len(rows)}</div>
        </div>
        <div class="summary-card">
            <div class="label">Total Amount</div>
            <div class="value positive">₹{total_amount:,.2f}</div>
        </div>
        <div class="summary-card">
            <div class="label">Total Advance</div>
            <div class="value primary">₹{advance_amount:,.2f}</div>
        </div>
        <div class="summary-card">
            <div class="label balance-label">🔴 BALANCE AMOUNT</div>
            <div class="value balance">₹{calculated_balance:,.2f}</div>
        </div>
        <div class="summary-card">
            <div class="label">Rounded Amount</div>
            <div class="value primary">₹{rounded_total:,.2f}</div>
        </div>
    </div>
    
    <hr style="margin: 20px 0; border: 1px dashed #dee2e6;">'''
    
    if rows:
        html += '''
    <table>
        <thead>
            <tr>
                <th>#</th>
                <th>Date</th>
                <th>Start</th>
                <th>Stop</th>
                <th>Time</th>
                <th>Rate (₹)</th>
                <th>Amount (₹)</th>
                <th>Advance (₹)</th>
                <th>Balance (₹)</th>
                <th>Reason</th>
                <th>Phone</th>
                <th>Status</th>
            </tr>
        </thead>
        <tbody>'''
        
        for idx, row in enumerate(rows, 1):
            if isinstance(row, dict):
                date_str = row['s_date'].strftime('%d-%m-%Y') if row['s_date'] else ''
                start_time = row['start_time'].strftime('%H:%M') if row['start_time'] else ''
                stop_time = row['stop_time'].strftime('%H:%M') if row['stop_time'] else ''
                rate = float(row['rate'] or 0)
                amount = float(row['amount'] or 0)
                advance = float(row['advance_amount'] or 0)
                record_balance = float(row['balance_amount'] or 0)
                status = 'PAID' if row['paid'] else 'UNPAID'
                status_class = 'status-paid' if row['paid'] else 'status-unpaid'
                reason = row['reason'] or '-'
                phone = format_phone(row.get('phone'))
                total_time = row.get('duration', '')
            else:
                date_str = row[0].strftime('%d-%m-%Y') if row[0] else ''
                start_time = row[1].strftime('%H:%M') if row[1] else ''
                stop_time = row[2].strftime('%H:%M') if row[2] else ''
                total_time = ''
                if row[3]:
                    if hasattr(row[3], 'total_seconds'):
                        hours = row[3].total_seconds() // 3600
                        minutes = (row[3].total_seconds() % 3600) // 60
                        total_time = f'{int(hours):02d}:{int(minutes):02d}'
                    else:
                        total_time = str(row[3])
                rate = float(row[4] or 0)
                amount = float(row[5] or 0)
                advance = float(row[6] or 0)
                record_balance = float(row[7] or 0)
                status = 'PAID' if row[8] else 'UNPAID'
                status_class = 'status-paid' if row[8] else 'status-unpaid'
                reason = row[9] if len(row) > 9 and row[9] else '-'
                phone = format_phone(row[10] if len(row) > 10 else None)
            
            html += f'''
        <tr>
            <td>{idx}</td>
            <td>{date_str}</td>
            <td>{start_time}</td>
            <td>{stop_time}</td>
            <td>{total_time}</td>
            <td>₹{rate:,.2f}</td>
            <td>₹{amount:,.2f}</td>
            <td>₹{advance:,.2f}</td>
            <td>₹{record_balance:,.2f}</td>
            <td>{reason}</td>
            <td class="phone-cell">{phone}</td>
            <td><span class="{status_class}">{status}</span></td>
        </tr>'''
        
        html += '''
        </tbody>
    </table>'''
        
        html += f'''
    <div class="advance-note">
        * Balance Amount = Total Amount - Total Advance = ₹{total_amount:,.2f} - ₹{advance_amount:,.2f} = ₹{calculated_balance:,.2f}
    </div>'''
    else:
        html += '''
    <div class="no-records">
        <h3>📭 No records found</h3>
        <p>No records available for this farmer</p>
    </div>'''
    
    html += '''
    <div class="payment-history">
        <h3>📋 PAYMENT HISTORY</h3>'''
    
    if payments:
        html += '''
        <table>
            <thead>
                <tr>
                    <th>Payment Date</th>
                    <th>Record Date</th>
                    <th>Amount (₹)</th>
                    <th>Method</th>
                    <th>Transaction ID</th>
                    <th>Notes</th>
                </tr>
            </thead>
            <tbody>'''
        
        for p in payments:
            payment_date = p[0].strftime('%d-%m-%Y %H:%M') if p[0] else ''
            record_date = p[1].strftime('%d-%m-%Y') if p[1] else ''
            amount = float(p[2] or 0)
            method = p[3].upper() if p[3] else 'CASH'
            trans_id = p[4] or 'N/A'
            notes = p[5] or ''
            
            method_badge = f'<span class="badge badge-{method.lower()}">{method}</span>'
            
            html += f'''
        <tr>
            <td>{payment_date}</td>
            <td>{record_date}</td>
            <td><strong>₹{amount:,.2f}</strong></td>
            <td>{method_badge}</td>
            <td>{trans_id}</td>
            <td>{notes}</td>
        </tr>'''
        
        html += '''
            </tbody>
        </table>'''
        
        html += f'''
        <div class="payment-summary">
            <div><strong>Total Payments:</strong> {total_payments}</div>
            <div><strong>Total Amount Paid:</strong> ₹{total_paid_amount:,.2f}</div>
        </div>'''
    else:
        html += '''
        <div class="no-records" style="padding:20px;">
            <p>No payment history found</p>
        </div>'''
    
    html += f'''
    </div>
    
    <div class="footer">
        Generated by Daily Tractor Management System<br>
        Report ID: {report_id} | Generated on {current_date}
    </div>
    
    <div style="text-align:center;margin-top:20px;" class="no-print">
        <button onclick="window.print()" style="background:#2c3e50;color:white;padding:12px 30px;border:none;border-radius:8px;font-size:16px;cursor:pointer;">
            🖨️ Print / Save as PDF
        </button>
    </div>
</body>
</html>'''
    
    return html

# ==================== DATABASE INITIALIZATION ====================

def init_db():
    conn = get_db()
    if not conn:
        logger.error("❌ Failed to connect to database - tables not created")
        return
    
    try:
        cursor = conn.cursor()
        logger.info("🔧 Creating database tables...")
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS daily_tractor (
                sl_no SERIAL PRIMARY KEY,
                s_date DATE NOT NULL,
                iname VARCHAR(150) NOT NULL,
                phone VARCHAR(10) NOT NULL,
                start_time TIME NOT NULL,
                stop_time TIME NOT NULL,
                total_time INTERVAL GENERATED ALWAYS AS (stop_time - start_time) STORED,
                rate NUMERIC(10,2) NOT NULL,
                amount NUMERIC(12,2) GENERATED ALWAYS AS ((EXTRACT(EPOCH FROM stop_time - start_time) / 3600 * rate)) STORED,
                advance_amount NUMERIC(10,2) NOT NULL DEFAULT 0,
                balance_amount NUMERIC(12,2) GENERATED ALWAYS AS ((EXTRACT(EPOCH FROM stop_time - start_time) / 3600 * rate - advance_amount)) STORED,
                reason VARCHAR(150) NOT NULL,
                paid BOOLEAN DEFAULT FALSE,
                remaining_balance NUMERIC(10,2) DEFAULT 0,
                payment_verified_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        logger.info("✅ daily_tractor table created")
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS payment_transactions (
                id SERIAL PRIMARY KEY,
                sl_no INTEGER REFERENCES daily_tractor(sl_no) ON DELETE CASCADE,
                payment_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                amount NUMERIC(10,2) NOT NULL,
                method VARCHAR(20) NOT NULL,
                transaction_id VARCHAR(50) NOT NULL,
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        logger.info("✅ payment_transactions table created")
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS tractor_operations (
                id SERIAL PRIMARY KEY,
                sdate DATE NOT NULL,
                name_batayidar VARCHAR(100) NOT NULL,
                crop VARCHAR(50) NOT NULL,
                start_time TIME NOT NULL,
                stop_time TIME NOT NULL,
                rate DECIMAL(10,2) NOT NULL,
                reason VARCHAR(50) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        logger.info("✅ tractor_operations table created")
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS batayidar (
                id SERIAL PRIMARY KEY,
                name_batayidar VARCHAR(100) NOT NULL UNIQUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        logger.info("✅ batayidar table created")
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS crop (
                id SERIAL PRIMARY KEY,
                crop VARCHAR(100) NOT NULL UNIQUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        logger.info("✅ crop table created")
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS diesel_purchase (
                sl_no SERIAL PRIMARY KEY,
                purchase_date DATE NOT NULL,
                amount DECIMAL(10,2) NOT NULL,
                rate DECIMAL(10,2) NOT NULL,
                total_liter DECIMAL(10,2) GENERATED ALWAYS AS (amount / rate) STORED,
                product VARCHAR(50) DEFAULT 'Diesel',
                bill_image_path VARCHAR(255),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        logger.info("✅ diesel_purchase table created")
        
        conn.commit()
        logger.info("✅ All database tables initialized successfully!")
        
        cursor.execute("SELECT COUNT(*) FROM diesel_purchase")
        count = cursor.fetchone()[0]
        if count == 0:
            logger.info("📝 Adding sample diesel data...")
            cursor.execute('''
                INSERT INTO diesel_purchase (purchase_date, amount, rate, product)
                VALUES 
                    (CURRENT_DATE, 5000, 85, 'Diesel'),
                    (CURRENT_DATE - INTERVAL '1 day', 3700, 90, 'Diesel'),
                    (CURRENT_DATE - INTERVAL '2 days', 1100, 101.88, 'Diesel')
            ''')
            conn.commit()
            logger.info("✅ Sample diesel data added")
        
        cursor.execute("SELECT COUNT(*) FROM tractor_operations")
        count = cursor.fetchone()[0]
        if count == 0:
            logger.info("📝 Adding sample tractor operation data...")
            cursor.execute('''
                INSERT INTO tractor_operations (sdate, name_batayidar, crop, start_time, stop_time, rate, reason)
                VALUES 
                    (CURRENT_DATE, 'रमेश कुमार', 'गेहूं', '08:00:00', '12:00:00', 500, 'plowing'),
                    (CURRENT_DATE - INTERVAL '1 day', 'सुरेश पटेल', 'चावल', '09:00:00', '13:00:00', 600, 'harrowing'),
                    (CURRENT_DATE - INTERVAL '2 days', 'महेश सिंह', 'मक्का', '10:00:00', '14:00:00', 550, 'sowing')
            ''')
            conn.commit()
            logger.info("✅ Sample tractor operation data added")
        
        cursor.execute("SELECT COUNT(*) FROM daily_tractor")
        count = cursor.fetchone()[0]
        if count == 0:
            logger.info("📝 Adding sample daily tractor data...")
            cursor.execute('''
                INSERT INTO daily_tractor (s_date, iname, phone, start_time, stop_time, rate, advance_amount, reason)
                VALUES 
                    (CURRENT_DATE, 'नितिन दिमोले', '8989898989', '12:35:00', '14:36:00', 1000, 500, 'plowing'),
                    (CURRENT_DATE - INTERVAL '1 day', 'दीपक कुमार', '9876543210', '09:00:00', '13:00:00', 800, 300, 'harrowing')
            ''')
            conn.commit()
            logger.info("✅ Sample daily tractor data added")
        
    except Exception as e:
        logger.error(f"❌ Error initializing database: {e}")
        logger.error(traceback.format_exc())
    finally:
        cursor.close()
        conn.close()

# ==================== ROUTES ====================

@app.route('/')
def index():
    diesel_stats = get_diesel_stats()
    tractor_stats = get_tractor_stats()
    operation_stats = get_operation_stats()
    
    return render_template('main_menu.html', 
                         diesel_stats=diesel_stats,
                         tractor_stats=tractor_stats,
                         operation_stats=operation_stats,
                         upi_id=UPI_ID,
                         upi_name=UPI_NAME,
                         now=datetime.now())

@app.route('/diesel')
def diesel_index():
    purchases = get_all_purchases()
    summary = get_summary()
    
    return render_template('diesel_index.html', 
                         purchases=purchases, 
                         summary=summary)

@app.route('/diesel/add', methods=['GET', 'POST'])
def add_purchase_route():
    if request.method == 'POST':
        try:
            purchase_date = request.form.get('purchase_date')
            amount = float(request.form.get('amount'))
            rate = float(request.form.get('rate'))
            product = request.form.get('product', 'Diesel')
            
            if amount <= 0 or rate <= 0:
                flash('Amount and Rate must be greater than 0!', 'danger')
                return redirect(url_for('add_purchase_route'))
            
            bill_image_path = None
            if 'bill_image' in request.files:
                file = request.files['bill_image']
                if file and file.filename != '' and allowed_file(file.filename):
                    filename = secure_filename(file.filename)
                    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                    filename = f"bill_{timestamp}_{filename}"
                    file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                    file.save(file_path)
                    bill_image_path = file_path
                    flash('Bill image uploaded successfully!', 'success')
            
            success = add_purchase(purchase_date, amount, rate, product, bill_image_path)
            
            if success:
                flash('✅ Diesel purchase added successfully!', 'success')
            else:
                flash('❌ Failed to add purchase.', 'danger')
            
            return redirect(url_for('add_purchase_route'))
            
        except ValueError:
            flash('Invalid amount or rate.', 'danger')
        except Exception as e:
            flash(f'Error: {str(e)}', 'danger')
        
        return redirect(url_for('add_purchase_route'))
    
    purchases = get_all_purchases()
    return render_template('diesel_add.html', 
                         edit_mode=False, 
                         now=datetime.now(),
                         purchases=purchases)

@app.route('/diesel/delete/<int:sl_no>')
def delete_purchase_route(sl_no):
    success = delete_purchase_by_id(sl_no)
    if success:
        flash('Purchase deleted successfully!', 'success')
    else:
        flash('Failed to delete purchase.', 'danger')
    return redirect(url_for('add_purchase_route'))

@app.route('/diesel/edit/<int:sl_no>', methods=['GET', 'POST'])
def edit_purchase_route(sl_no):
    if request.method == 'POST':
        try:
            purchase_date = request.form.get('purchase_date')
            amount = float(request.form.get('amount'))
            rate = float(request.form.get('rate'))
            product = request.form.get('product', 'Diesel')
            
            if amount <= 0 or rate <= 0:
                flash('Amount and Rate must be greater than 0!', 'danger')
                return redirect(url_for('edit_purchase_route', sl_no=sl_no))
            
            bill_image_path = None
            if 'bill_image' in request.files:
                file = request.files['bill_image']
                if file and file.filename != '' and allowed_file(file.filename):
                    filename = secure_filename(file.filename)
                    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                    filename = f"bill_{timestamp}_{filename}"
                    file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                    file.save(file_path)
                    bill_image_path = file_path
                    flash('Bill image updated!', 'success')
            
            success = update_purchase(sl_no, purchase_date, amount, rate, product, bill_image_path)
            
            if success:
                flash('✅ Purchase updated successfully!', 'success')
            else:
                flash('❌ Failed to update purchase.', 'danger')
            
            return redirect(url_for('edit_purchase_route', sl_no=sl_no))
            
        except ValueError:
            flash('Invalid amount or rate.', 'danger')
        except Exception as e:
            flash(f'Error: {str(e)}', 'danger')
        
        return redirect(url_for('edit_purchase_route', sl_no=sl_no))
    
    purchase = get_purchase_by_id(sl_no)
    if not purchase:
        flash('Purchase not found!', 'danger')
        return redirect(url_for('add_purchase_route'))
    
    purchases = get_all_purchases()
    
    return render_template('diesel_add.html', 
                         purchase=purchase, 
                         edit_mode=True, 
                         now=datetime.now(),
                         purchases=purchases)

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    filename = secure_filename(filename)
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/daily_tractor')
def daily_tractor_form():
    reason_options = [
        ('plowing', '🌾 बखरनी'),
        ('harrowing', '🚜 प्लाउ'),
        ('sowing', '🌱 बुवाई'),
        ('Threshar', '💊 थ्रेशिंग'),
        ('transport', '🚛 परिवहन'),
        ('other', '📋 अन्य')
    ]
    
    farmer_names = get_farmer_names()
    entries = get_daily_tractor_entries(True)
    
    total_amount = 0.0
    advance_amount = 0.0
    balance_amount = 0.0
    
    for entry in entries:
        total_amount += float(entry.get('amount', 0))
        advance_amount += float(entry.get('advance_amount', 0))
        balance_amount += float(entry.get('balance_amount', 0))
    
    calculated_balance = total_amount - advance_amount
    
    return render_template('daily_tractor.html',
        reason_options=reason_options,
        entries=entries,
        farmer_names=farmer_names,
        total_amount=total_amount,
        advance_amount=advance_amount,
        balance_amount=calculated_balance,
        current_datetime=datetime.now(),
        UPI_ID=UPI_ID,
        UPI_NAME=UPI_NAME,
        UPI_MERCHANT=UPI_MERCHANT
    )

@app.route('/daily_tractor_report')
def daily_tractor_report():
    selected_farmer = request.args.get("farmer_name", "")
    show_full = request.args.get("show_full_statement", "true") == "true"
    
    farmer_names = get_farmer_names()
    
    rows = []
    total_amount = 0.0
    advance_amount = 0.0
    balance_amount = 0.0
    paid_records = 0
    unpaid_records = 0
    payment_history = []
    
    if selected_farmer:
        rows = get_daily_tractor_entries(show_full, selected_farmer)
        total_amount, advance_amount, balance_amount = get_farmer_totals(selected_farmer)
        payment_history = get_payment_history(selected_farmer)
        
        for entry in rows:
            if entry.get('paid', False):
                paid_records += 1
            else:
                unpaid_records += 1
    else:
        rows = get_daily_tractor_entries(True)
        for entry in rows:
            total_amount += float(entry.get('amount', 0))
            advance_amount += float(entry.get('advance_amount', 0))
            balance_amount += float(entry.get('balance_amount', 0))
            if entry.get('paid', False):
                paid_records += 1
            else:
                unpaid_records += 1
    
    calculated_balance = total_amount - advance_amount
    
    return render_template('daily_tractor_report.html',
        rows=rows,
        farmer_names=farmer_names,
        selected_farmer=selected_farmer or "",
        show_full=show_full,
        total_amount=total_amount,
        advance_amount=advance_amount,
        balance_amount=calculated_balance,
        unpaid_balance=calculated_balance,
        paid_records=paid_records,
        unpaid_records=unpaid_records,
        payment_history=payment_history,
        current_datetime=datetime.now(),
        UPI_ID=UPI_ID,
        UPI_NAME=UPI_NAME,
        UPI_MERCHANT=UPI_MERCHANT
    )

@app.route('/insert/daily_tractor', methods=['POST'])
def insert_daily_tractor():
    if request.method == 'POST':
        conn = get_db()
        if not conn:
            flash("Database connection error!", "error")
            return redirect(url_for('daily_tractor_form'))
        
        try:
            s_date = request.form['s_date']
            iname = request.form['iname']
            phone = request.form['phone']
            advance_amount = float(request.form['advance_amount'])
            start_time = request.form['start_time']
            stop_time = request.form['stop_time']
            rate = float(request.form['rate'])
            reason = request.form['reason']
            
            if not phone or len(phone) != 10 or not phone.isdigit():
                flash('कृपया 10 अंकों का सही फोन नंबर दर्ज करें!', 'error')
                return redirect(url_for('daily_tractor_form'))
            
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO daily_tractor (s_date, iname, phone, advance_amount, start_time, stop_time, rate, reason)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ''', (s_date, iname, phone, advance_amount, start_time, stop_time, rate, reason))
            conn.commit()
            cursor.close()
            conn.close()
            
            flash('✅ डेली ट्रैक्टर प्रविष्टि सफलतापूर्वक सहेजी गई!', 'success')
            return redirect(url_for('daily_tractor_form'))
        except Exception as e:
            flash(f'Error: {str(e)}', 'error')
            return redirect(url_for('daily_tractor_form'))

@app.route("/submit_cash_payment", methods=["POST"])
def submit_cash_payment():
    try:
        data = request.get_json()
        farmer_name = data.get("farmer_name", "").strip()
        payment_amount = float(data.get("payment_amount", 0))
        payment_notes = data.get("payment_notes", "")
        
        if not farmer_name or payment_amount <= 0:
            return jsonify({"success": False, "error": "Invalid payment data"})
        
        conn = get_db_connection()
        if not conn:
            return jsonify({"success": False, "error": "Database connection failed"})
        
        cur = conn.cursor()
        try:
            cur.execute("SET client_encoding = 'UTF8'")
            
            cur.execute("""
                SELECT sl_no, balance_amount FROM daily_tractor 
                WHERE iname ILIKE %s AND paid = false AND balance_amount > 0
                ORDER BY s_date ASC
            """, (farmer_name,))
            unpaid_records = cur.fetchall()
            
            if not unpaid_records:
                return jsonify({"success": False, "error": "No unpaid records found"})
            
            total_unpaid = sum(float(r[1]) for r in unpaid_records)
            if payment_amount > total_unpaid:
                return jsonify({
                    "success": False, 
                    "error": f"Amount exceeds total unpaid balance of ₹{total_unpaid:,.2f}"
                })
            
            confirmation_no = f"PA-{datetime.now().strftime('%Y%m%d%H%M%S')}-{random.randint(1000, 9999)}"
            processed_count = 0
            remaining_amount = payment_amount
            transaction_id = f"CASH_{datetime.now().strftime('%Y%m%d%H%M%S')}_{random.randint(100,999)}"
            
            for sl_no, balance in unpaid_records:
                if remaining_amount <= 0:
                    break
                current_balance = float(balance)
                
                if remaining_amount >= current_balance:
                    pay_amount = current_balance
                    remaining_amount -= current_balance
                    paid_status = True
                else:
                    pay_amount = remaining_amount
                    remaining_amount = 0
                    paid_status = False
                
                cur.execute("""
                    INSERT INTO payment_transactions 
                    (sl_no, amount, method, transaction_id, notes, payment_date)
                    VALUES (%s, %s, 'cash', %s, %s, %s)
                """, (sl_no, pay_amount, transaction_id, payment_notes, datetime.now()))
                
                cur.execute("""
                    UPDATE daily_tractor 
                    SET advance_amount = COALESCE(advance_amount, 0) + %s,
                        paid = %s,
                        payment_verified_at = %s 
                    WHERE sl_no = %s
                """, (pay_amount, paid_status, datetime.now(), sl_no))
                
                processed_count += 1
            
            conn.commit()
            
            payment_confirmations[confirmation_no] = {
                "farmer_name": farmer_name,
                "amount": payment_amount,
                "payment_method": "cash",
                "transaction_id": transaction_id,
                "payment_notes": payment_notes,
                "confirmation_no": confirmation_no,
                "status": "VERIFIED",
                "processed_records": processed_count
            }
            
            return jsonify({
                "success": True,
                "message": f"✅ Cash payment of ₹{payment_amount:,.2f} applied to {processed_count} records",
                "confirmation_no": confirmation_no,
                "processed_records": processed_count,
                "advice_url": f"/generate_payment_advice?confirmation_no={confirmation_no}"
            })
            
        except Exception as e:
            conn.rollback()
            return jsonify({"success": False, "error": str(e)})
        finally:
            cur.close()
            conn.close()
            
    except Exception as e:
        logger.error(f"Error in cash payment: {e}")
        return jsonify({"success": False, "error": str(e)})

@app.route("/submit_upi_payment", methods=["POST"])
def submit_upi_payment():
    try:
        data = request.get_json()
        farmer_name = data.get("farmer_name", "").strip()
        payment_amount = float(data.get("payment_amount", 0))
        payment_notes = data.get("payment_notes", "")
        transaction_id = data.get("transaction_id", f"UPI_{datetime.now().strftime('%Y%m%d%H%M%S')}_{random.randint(100,999)}")
        
        if not farmer_name or payment_amount <= 0:
            return jsonify({"success": False, "error": "Invalid payment data"})
        
        conn = get_db_connection()
        if not conn:
            return jsonify({"success": False, "error": "Database connection failed"})
        
        cur = conn.cursor()
        try:
            cur.execute("SET client_encoding = 'UTF8'")
            
            cur.execute("""
                SELECT sl_no, balance_amount FROM daily_tractor 
                WHERE iname ILIKE %s AND paid = false AND balance_amount > 0
                ORDER BY s_date ASC
            """, (farmer_name,))
            unpaid_records = cur.fetchall()
            
            if not unpaid_records:
                return jsonify({"success": False, "error": "No unpaid records found"})
            
            total_unpaid = sum(float(r[1]) for r in unpaid_records)
            if payment_amount > total_unpaid:
                return jsonify({
                    "success": False, 
                    "error": f"Amount exceeds total unpaid balance of ₹{total_unpaid:,.2f}"
                })
            
            confirmation_no = f"UPI-{datetime.now().strftime('%Y%m%d%H%M%S')}-{random.randint(1000, 9999)}"
            processed_count = 0
            remaining_amount = payment_amount
            
            for sl_no, balance in unpaid_records:
                if remaining_amount <= 0:
                    break
                current_balance = float(balance)
                
                if remaining_amount >= current_balance:
                    pay_amount = current_balance
                    remaining_amount -= current_balance
                    paid_status = True
                else:
                    pay_amount = remaining_amount
                    remaining_amount = 0
                    paid_status = False
                
                cur.execute("""
                    INSERT INTO payment_transactions 
                    (sl_no, amount, method, transaction_id, notes, payment_date)
                    VALUES (%s, %s, 'upi', %s, %s, %s)
                """, (sl_no, pay_amount, transaction_id, payment_notes, datetime.now()))
                
                cur.execute("""
                    UPDATE daily_tractor 
                    SET advance_amount = COALESCE(advance_amount, 0) + %s,
                        paid = %s,
                        payment_verified_at = %s 
                    WHERE sl_no = %s
                """, (pay_amount, paid_status, datetime.now(), sl_no))
                
                processed_count += 1
            
            conn.commit()
            
            payment_confirmations[confirmation_no] = {
                "farmer_name": farmer_name,
                "amount": payment_amount,
                "payment_method": "upi",
                "transaction_id": transaction_id,
                "payment_notes": payment_notes,
                "confirmation_no": confirmation_no,
                "status": "VERIFIED",
                "processed_records": processed_count
            }
            
            return jsonify({
                "success": True,
                "message": f"✅ UPI payment of ₹{payment_amount:,.2f} applied to {processed_count} records",
                "confirmation_no": confirmation_no,
                "processed_records": processed_count,
                "advice_url": f"/generate_payment_advice?confirmation_no={confirmation_no}"
            })
            
        except Exception as e:
            conn.rollback()
            return jsonify({"success": False, "error": str(e)})
        finally:
            cur.close()
            conn.close()
            
    except Exception as e:
        logger.error(f"Error in UPI payment: {e}")
        return jsonify({"success": False, "error": str(e)})

@app.route("/download_tractor_report_pdf", methods=["GET"])
def download_tractor_report_pdf():
    try:
        farmer_name = request.args.get("farmer_name", "")
        show_full = request.args.get("full_statement", "false") == "true"
        
        if not farmer_name:
            return "No farmer selected", 400
        
        conn = get_db_connection()
        if not conn:
            return "Database connection failed"
        
        cur = conn.cursor()
        try:
            cur.execute("SET client_encoding = 'UTF8'")
            
            if show_full:
                cur.execute("""
                    SELECT s_date, start_time, stop_time, total_time, 
                           rate, amount, advance_amount, balance_amount, reason, paid, phone
                    FROM daily_tractor WHERE iname ILIKE %s 
                    ORDER BY s_date DESC, start_time DESC
                """, (farmer_name,))
            else:
                cur.execute("""
                    SELECT s_date, start_time, stop_time, total_time, 
                           rate, amount, advance_amount, balance_amount, reason, paid, phone
                    FROM daily_tractor WHERE iname ILIKE %s AND paid = false AND balance_amount > 0
                    ORDER BY s_date DESC, start_time DESC
                """, (farmer_name,))
            
            rows = cur.fetchall()
            
            total_amount, advance_amount, balance_amount = get_farmer_totals(farmer_name)
            calculated_balance = total_amount - advance_amount
            
        except Exception as e:
            return f"Database error: {e}"
        finally:
            cur.close()
            conn.close()
        
        html_content = generate_tractor_report_html(farmer_name, rows, total_amount, advance_amount, calculated_balance, calculated_balance, show_full)
        return html_content
            
    except Exception as e:
        logger.error(f"Error generating report: {e}")
        return f"Error: {e}", 500

@app.route("/generate_payment_advice", methods=["GET"])
def generate_payment_advice():
    confirmation_no = request.args.get("confirmation_no", "")
    if not confirmation_no or confirmation_no not in payment_confirmations:
        return "Invalid confirmation number", 404
    data = payment_confirmations[confirmation_no]
    
    return f'''
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>Payment Receipt</title>
        <style>
            body {{ font-family: Arial, sans-serif; padding: 40px; background: #f0f2f5; }}
            .container {{ max-width: 600px; margin: 0 auto; background: white; padding: 30px; border-radius: 12px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
            h1 {{ color: #27ae60; text-align: center; }}
            .detail {{ padding: 10px 0; border-bottom: 1px solid #eee; }}
            .label {{ font-weight: bold; color: #555; }}
            .status {{ color: #27ae60; font-weight: bold; }}
            .footer {{ text-align: center; margin-top: 20px; color: #999; font-size: 12px; }}
            .btn {{ display: inline-block; background: #27ae60; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; margin-top: 10px; }}
            .btn:hover {{ background: #219a52; }}
            .no-print {{ display: inline; }}
            @media print {{ .no-print {{ display: none !important; }} }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🧾 Payment Receipt</h1>
            <div class="detail"><span class="label">Confirmation:</span> {confirmation_no}</div>
            <div class="detail"><span class="label">Farmer:</span> {data.get('farmer_name')}</div>
            <div class="detail"><span class="label">Amount:</span> ₹{data.get('amount', 0):,.2f}</div>
            <div class="detail"><span class="label">Method:</span> {data.get('payment_method', '').upper()}</div>
            <div class="detail"><span class="label">Transaction:</span> {data.get('transaction_id')}</div>
            <div class="detail"><span class="label">Date:</span> {datetime.now().strftime('%d %B %Y %I:%M %p')}</div>
            <div class="detail"><span class="label">Status:</span> <span class="status">✅ VERIFIED</span></div>
            <div class="detail"><span class="label">Notes:</span> {data.get('payment_notes', 'N/A')}</div>
            <div style="text-align:center;margin-top:20px;">
                <button class="btn no-print" onclick="window.print()">🖨️ Print / Save as PDF</button>
            </div>
            <div class="footer">Daily Tractor Management System<br>Generated on {datetime.now().strftime('%d %B %Y at %I:%M %p')}</div>
        </div>
    </body>
    </html>
    '''

@app.route('/tractor_operation')
def tractor_operation_form():
    conn = get_db()
    if not conn:
        flash("Database connection error!", "error")
        return render_template('tractor_operation.html', names=[], crops=[], reason_options=[], entries=[], now=datetime.now())
    
    try:
        cursor = conn.cursor()
        
        cursor.execute('SELECT DISTINCT name_batayidar FROM batayidar ORDER BY name_batayidar')
        names_rows = cursor.fetchall()
        
        cursor.execute('SELECT crop FROM crop ORDER BY crop')
        crops_rows = cursor.fetchall()
        
        cursor.close()
        conn.close()
        
        names = [row[0] for row in names_rows if row[0]]
        crops = [row[0] for row in crops_rows if row[0]]
        
        reason_options = [
            ('plowing', '🌾 बखरनी'),
            ('harrowing', '🚜 प्लाउ'),
            ('sowing', '🌱 बुवाई'),
            ('Threshar', '💊 थ्रेशिंग'),
            ('transport', '🚛 परिवहन'),
            ('other', '📋 अन्य')
        ]
        
        if not names:
            names = ['रमेश कुमार', 'सुरेश पटेल', 'महेश सिंह', 'दिनेश यादव']
        
        if not crops:
            crops = ['गेहूं', 'चावल', 'मक्का', 'सोयाबीन', 'जौ', 'कपास', 'गन्ना', 'सब्जियां']
        
        entries = get_tractor_operation_entries()
        
        return render_template('tractor_operation.html', 
                             names=names, 
                             crops=crops, 
                             reason_options=reason_options, 
                             entries=entries,
                             now=datetime.now())
    except Exception as e:
        logger.error(f"Error loading data: {str(e)}")
        flash(f'Error loading data: {str(e)}', 'error')
        return render_template('tractor_operation.html', names=[], crops=[], reason_options=[], entries=[], now=datetime.now())

@app.route('/insert/tractor_operation', methods=['POST'])
def insert_tractor_operation():
    if request.method == 'POST':
        conn = get_db()
        if not conn:
            flash("Database connection error!", "error")
            return redirect(url_for('tractor_operation_form'))
        
        try:
            sdate = request.form['sdate']
            name_batayidar = request.form['name_batayidar']
            crop = request.form['crop']
            start_time = request.form['start_time']
            stop_time = request.form['stop_time']
            rate = float(request.form['rate'])
            reason = request.form['reason']
            
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO tractor_operations (sdate, name_batayidar, crop, start_time, stop_time, rate, reason)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            ''', (sdate, name_batayidar, crop, start_time, stop_time, rate, reason))
            conn.commit()
            cursor.close()
            conn.close()
            
            flash('✅ अपनी प्रविष्टि सफलतापूर्वक सहेजी गई!', 'success')
            return redirect(url_for('tractor_operation_form'))
        except Exception as e:
            flash(f'Error: {str(e)}', 'error')
            return redirect(url_for('tractor_operation_form'))

@app.route('/debug-simple')
def debug_simple():
    try:
        conn = get_db_connection()
        if not conn:
            return "❌ Database connection failed!"
        
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM diesel_purchase")
        count = cursor.fetchone()[0]
        cursor.close()
        conn.close()
        return f"✅ Database connected! Found {count} records."
    except Exception as e:
        return f"❌ Error: {str(e)}"

@app.route('/api/farmers')
def api_farmers():
    return jsonify(get_farmer_names())

@app.route('/api/farmer_totals')
def api_farmer_totals():
    farmer_name = request.args.get("farmer_name", "")
    if not farmer_name:
        return jsonify({"error": "No farmer name provided"}), 400
    total, advance, balance = get_farmer_totals(farmer_name)
    return jsonify({
        "farmer_name": farmer_name,
        "total_amount": total,
        "advance_amount": advance,
        "balance_amount": total - advance
    })

# ==================== MAIN ====================

if __name__ == '__main__':
    logger.info("🚀 Starting application...")
    init_db()
    
    port = int(os.environ.get("PORT", 5000))
    print("\n" + "="*70)
    print("🚜 UNIFIED AGRICULTURE MANAGEMENT SYSTEM")
    print("="*70)
    print(f"🌐 Server: http://0.0.0.0:{port}")
    print("\n📍 Available Routes:")
    print(f"  - http://0.0.0.0:{port}/ (Main Menu)")
    print(f"  - http://0.0.0.0:{port}/diesel (Diesel Purchase)")
    print(f"  - http://0.0.0.0:{port}/diesel/add (Add Diesel Purchase)")
    print(f"  - http://0.0.0.0:{port}/daily_tractor (Daily Tractor Entry Form)")
    print(f"  - http://0.0.0.0:{port}/daily_tractor_report (Daily Tractor Report)")
    print(f"  - http://0.0.0.0:{port}/tractor_operation (Tractor Operation)")
    print(f"  - http://0.0.0.0:{port}/download_tractor_report_pdf (Generate Reports)")
    print(f"\n💰 UPI ID: {UPI_ID}")
    print("="*70 + "\n")
    
    app.run(debug=True, host='0.0.0.0', port=port)