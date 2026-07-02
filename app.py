from flask import Flask, render_template, request, redirect, url_for, flash, send_from_directory, jsonify
from datetime import datetime, timedelta
import psycopg2
from psycopg2.extras import RealDictCursor
import os
import logging
import traceback
from werkzeug.utils import secure_filename

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "your-secret-key-here")

# Database configuration
DATABASE_URL = os.environ.get("DATABASE_URL")

DB_CONFIG = {
    "host": os.environ.get("DB_HOST", "localhost"),
    "database": os.environ.get("DB_NAME", "agriclture"),
    "user": os.environ.get("DB_USER", "postgres"),
    "password": os.environ.get("DB_PASSWORD", ""),
    "port": os.environ.get("DB_PORT", "5432")
}

# Upload configuration for diesel app
UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'pdf'}
MAX_CONTENT_LENGTH = 16 * 1024 * 1024

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = MAX_CONTENT_LENGTH

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# ==================== DATABASE FUNCTIONS ====================

def get_db():
    """Get database connection"""
    try:
        if DATABASE_URL:
            conn = psycopg2.connect(DATABASE_URL)
        else:
            conn = psycopg2.connect(**DB_CONFIG)

        return conn

    except Exception as e:
        logger.error(f"Database connection error: {e}")
        return None
    
def get_db_connection():
    """Alias for get_db for diesel app compatibility"""
    return get_db()

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# ==================== INITIALIZATION ====================

def init_db():
    """Initialize all database tables"""
    conn = get_db()
    if not conn:
        logger.error("Failed to connect to database")
        return
    
    try:
        cursor = conn.cursor()
        
        # Create daily_tractor table
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
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Create tractor_operations table
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
        
        # Create batayidar table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS batayidar (
                id SERIAL PRIMARY KEY,
                name_batayidar VARCHAR(100) NOT NULL UNIQUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Create crop table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS crop (
                id SERIAL PRIMARY KEY,
                crop VARCHAR(100) NOT NULL UNIQUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Create diesel_purchase table
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
        
        conn.commit()
        logger.info("All database tables initialized successfully")
        
        # Add sample data if tables are empty
        cursor.execute("SELECT COUNT(*) FROM diesel_purchase")
        count = cursor.fetchone()[0]
        if count == 0:
            cursor.execute('''
                INSERT INTO diesel_purchase (purchase_date, amount, rate, product)
                VALUES 
                    (CURRENT_DATE, 5000, 85, 'Diesel'),
                    (CURRENT_DATE - INTERVAL '1 day', 3700, 90, 'Diesel'),
                    (CURRENT_DATE - INTERVAL '2 days', 1100, 101.88, 'Diesel')
            ''')
            conn.commit()
            logger.info("Sample diesel data added")
        
        cursor.execute("SELECT COUNT(*) FROM tractor_operations")
        count = cursor.fetchone()[0]
        if count == 0:
            cursor.execute('''
                INSERT INTO tractor_operations (sdate, name_batayidar, crop, start_time, stop_time, rate, reason)
                VALUES 
                    (CURRENT_DATE, 'रमेश कुमार', 'गेहूं', '08:00:00', '12:00:00', 500, 'plowing'),
                    (CURRENT_DATE - INTERVAL '1 day', 'सुरेश पटेल', 'चावल', '09:00:00', '13:00:00', 600, 'harrowing'),
                    (CURRENT_DATE - INTERVAL '2 days', 'महेश सिंह', 'मक्का', '10:00:00', '14:00:00', 550, 'sowing')
            ''')
            conn.commit()
            logger.info("Sample tractor operation data added")
        
        cursor.execute("SELECT COUNT(*) FROM daily_tractor")
        count = cursor.fetchone()[0]
        if count == 0:
            cursor.execute('''
                INSERT INTO daily_tractor (s_date, iname, phone, start_time, stop_time, rate, advance_amount, reason)
                VALUES 
                    (CURRENT_DATE, 'नितिन दिमोले', '8989898989', '12:35:00', '14:36:00', 1000, 500, 'plowing'),
                    (CURRENT_DATE - INTERVAL '1 day', 'दीपक कुमार', '9876543210', '09:00:00', '13:00:00', 800, 300, 'harrowing')
            ''')
            conn.commit()
            logger.info("Sample daily tractor data added")
        
    except Exception as e:
        logger.error(f"Error initializing database: {e}")
        logger.error(traceback.format_exc())
    finally:
        cursor.close()
        conn.close()

# ==================== STATISTICS FUNCTIONS ====================

def get_diesel_stats():
    """Get diesel purchase statistics"""
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
    """Get daily tractor statistics"""
    conn = get_db()
    if not conn:
        return {'total_entries': 0, 'total_amount': 0, 'balance_amount': 0}
    
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
        cursor.close()
        conn.close()
        
        return {
            'total_entries': result[0] or 0,
            'total_amount': float(result[1] or 0),
            'balance_amount': float(result[2] or 0)
        }
    except Exception as e:
        logger.error(f"Error getting tractor stats: {e}")
        return {'total_entries': 0, 'total_amount': 0, 'balance_amount': 0}

def get_operation_stats():
    """Get tractor operation statistics"""
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

# ==================== DAILY TRACTOR FUNCTIONS ====================

def calculate_duration(start_time, stop_time):
    """Calculate duration from time strings"""
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
        return f"{hours}h {minutes}m"
    except:
        return "0h 0m"

def get_reason_label(reason_value):
    """Convert reason value to display label"""
    reason_map = {
        'plowing': '🌾 बखरनी',
        'harrowing': '🚜 प्लाउ',
        'sowing': '🌱 बुवाई',
        'Threshar': '💊 थ्रेशिंग',
        'transport': '🚛 परिवहन',
        'other': '📋 अन्य'
    }
    return reason_map.get(reason_value, reason_value)

def get_daily_tractor_entries():
    """Get all daily tractor entries"""
    conn = get_db()
    if not conn:
        logger.error("No database connection")
        return []
    
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute('SELECT * FROM daily_tractor ORDER BY s_date DESC, start_time DESC')
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
                'rate': entry['rate'],
                'advance_amount': entry['advance_amount'],
                'reason': entry['reason'],
                'reason_label': get_reason_label(entry['reason']),
                'paid': entry.get('paid', False),
                'remaining_balance': entry.get('remaining_balance', 0),
                'duration': calculate_duration(entry['start_time'], entry['stop_time'])
            }
            result.append(entry_dict)
        
        return result
    except Exception as e:
        logger.error(f"Error fetching daily tractor entries: {e}")
        return []

# ==================== TRACTOR OPERATION FUNCTIONS ====================

def get_tractor_operation_entries():
    """Get all tractor operation entries"""
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
                'rate': entry['rate'],
                'reason': entry['reason'],
                'reason_label': get_reason_label(entry['reason'])
            }
            result.append(entry_dict)
        
        return result
    except Exception as e:
        logger.error(f"Error fetching tractor operation entries: {e}")
        logger.error(traceback.format_exc())
        return []

# ==================== DIESEL FUNCTIONS ====================

def get_all_purchases():
    """Fetch all diesel purchases"""
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
    """Get summary statistics for diesel"""
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

# ==================== ROUTES ====================

@app.route('/')
def index():
    """Main menu / Home page with statistics"""
    diesel_stats = get_diesel_stats()
    tractor_stats = get_tractor_stats()
    operation_stats = get_operation_stats()
    
    return render_template('main_menu.html', 
                         diesel_stats=diesel_stats,
                         tractor_stats=tractor_stats,
                         operation_stats=operation_stats)

# ==================== DIESEL ROUTES ====================

@app.route('/diesel')
def diesel_index():
    """Diesel purchase page"""
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
                flash('Diesel purchase added successfully!', 'success')
            else:
                flash('Failed to add purchase.', 'danger')
            
            return redirect(url_for('diesel_index'))
            
        except ValueError:
            flash('Invalid amount or rate.', 'danger')
        except Exception as e:
            flash(f'Error: {str(e)}', 'danger')
        
        return redirect(url_for('add_purchase_route'))
    
    return render_template('diesel_add.html', edit_mode=False, now=datetime.now())

@app.route('/diesel/delete/<int:sl_no>')
def delete_purchase_route(sl_no):
    success = delete_purchase_by_id(sl_no)
    if success:
        flash('Purchase deleted successfully!', 'success')
    else:
        flash('Failed to delete purchase.', 'danger')
    return redirect(url_for('diesel_index'))

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
                flash('Purchase updated successfully!', 'success')
            else:
                flash('Failed to update purchase.', 'danger')
            
            return redirect(url_for('diesel_index'))
            
        except ValueError:
            flash('Invalid amount or rate.', 'danger')
        except Exception as e:
            flash(f'Error: {str(e)}', 'danger')
        
        return redirect(url_for('edit_purchase_route', sl_no=sl_no))
    
    purchase = get_purchase_by_id(sl_no)
    if not purchase:
        flash('Purchase not found!', 'danger')
        return redirect(url_for('diesel_index'))
    
    return render_template('diesel_add.html', purchase=purchase, edit_mode=True, now=datetime.now())

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

# ==================== DAILY TRACTOR ROUTES ====================

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
    entries = get_daily_tractor_entries()
    return render_template('daily_tractor.html', reason_options=reason_options, entries=entries, now=datetime.now())

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
            
            # Validate phone number (10 digits)
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

# ==================== TRACTOR OPERATION ROUTES ====================

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
    """Simple debug route"""
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

# ==================== MAIN ====================

if __name__ == '__main__':
    init_db()
    port = int(os.environ.get("PORT", 5000))
    print("\n" + "="*60)
    print("🚀 Unified Agriculture Management System")
    print("="*60)
    print("📊 Database: " + DB_CONFIG['database'] + " on " + DB_CONFIG['host'])
    print("🌐 Server: http://localhost:" + str(port))
    print("\n📍 Available Routes:")
    print("  - http://localhost:" + str(port) + "/ (Main Menu)")
    print("  - http://localhost:" + str(port) + "/diesel (Diesel Purchase)")
    print("  - http://localhost:" + str(port) + "/diesel/add (Add Diesel Purchase)")
    print("  - http://localhost:" + str(port) + "/daily_tractor (Daily Tractor)")
    print("  - http://localhost:" + str(port) + "/tractor_operation (Tractor Operation)")
    print("="*60 + "\n")
    app.run(debug=True, host='0.0.0.0', port=port)