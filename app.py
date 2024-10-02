from flask import Flask, render_template, jsonify, request, redirect, url_for, session, flash
from functools import wraps
from confluent_kafka.admin import AdminClient, ClusterMetadata
from confluent_kafka import KafkaException
import psutil
import mysql.connector
import time
import threading
from multiprocessing import Semaphore
import hashlib

sem = Semaphore(1)

try:
    sem.acquire()
    # İşlem yap
finally:
    sem.release()

db_config = {
    'user': 'root',
    'password': '',
    'host': 'localhost',
    'database': 'kafkaMonitor'
}

app = Flask(__name__)
app.secret_key = 'gizli_anahtarınızı_buraya_yazın'  # Güçlü ve rastgele bir anahtar kullanın

# Kafka broker information
brokers = [
    {
        "broker": "localhost:9093",
        "host": "localhost",
        "port": 9093
    },
    {
        "broker": "localhost:9092",
        "host": "localhost",
        "port": 9092
    }
]


def get_brokers_from_db():
    """Get brokers from the database."""
    brokers = []
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()
        cursor.execute("SELECT host, port FROM broker")
        for (host, port) in cursor.fetchall():
            brokers.append({
                "broker": f"{host}:{port}",
                "host": host,
                "port": port
            })
    except mysql.connector.Error as err:
        print(f"Veritabanı hatası: {err}")
    finally:
        if conn:
            cursor.close()
            conn.close()
    return brokers

def get_broker_data(broker):
    """Get monitoring data for a specific broker."""
    admin_client = None
    cluster_metadata = None
    broker_id = None

    try:
        admin_client = AdminClient({'bootstrap.servers': broker['broker']})
        cluster_metadata = admin_client.list_topics(timeout=10)

        broker_list = cluster_metadata.brokers
        for broker_meta in broker_list.values():
            if broker_meta.host == broker['host'] and broker_meta.port == broker['port']:
                broker_id = broker_meta.id
                break

        topics = cluster_metadata.topics
        broker_status = "Aktif"

        topic_details = {}
        for topic_name, topic in topics.items():
            partitions = topic.partitions
            topic_info = {
                "partition_count": len(partitions),
                "partitions": []
            }

            for partition in partitions.values():
                partition_info = {
                    "partition_id": partition.id,
                    "leader": partition.leader,
                    "replicas": partition.replicas,
                    "isrs": partition.isrs
                }
                topic_info["partitions"].append(partition_info)

            topic_details[topic_name] = topic_info

    except KafkaException:
        broker_status = "Kapalı"
        topic_details = {}

    if broker_status == "Aktif":
        mem_info = psutil.virtual_memory()
        broker_data = {
            "broker_id": broker_id,
            "host": broker['host'],
            "port": broker['port'],
            "cpu_use": psutil.cpu_percent(interval=1),
            "memory_total_mb": mem_info.total / (1024 * 1024),
            "memory_used_mb": mem_info.used / (1024 * 1024),
            "memory_usage_percent": mem_info.percent,
            "disk_usage": psutil.disk_usage('/').percent,
            "network_traffic_in": psutil.net_io_counters().bytes_recv / (1024 * 1024),
            "network_traffic_out": psutil.net_io_counters().bytes_sent / (1024 * 1024),
            "response_time": None,
            "topic_details": topic_details
        }
    else:
        broker_data = {
            "broker_id": broker_id,
            "host": broker['host'],
            "port": broker['port'],
            "cpu_use": None,
            "memory_total_mb": None,
            "memory_used_mb": None,
            "memory_usage_percent": None,
            "disk_usage": None,
            "network_traffic_in": None,
            "network_traffic_out": None,
            "response_time": None,
            "topic_details": {}
        }

    broker_data["status"] = broker_status
    print(broker_data)
    return broker_data


def monitor_kafka():
    brokers = get_brokers_from_db()  # Fetch the latest brokers from the database
    data = []
    for broker in brokers:
        broker_data = get_broker_data(broker)
        data.append(broker_data)
    return data


def hash_password(password):
    return hashlib.md5(password.encode('utf-8')).hexdigest()


def validate_login(username, password):
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()
        query = "SELECT password FROM users WHERE username = %s"
        cursor.execute(query, (username,))
        result = cursor.fetchone()
        if result:
            stored_hashed_password = result[0]
            return stored_hashed_password == hash_password(password)
        return False
    except mysql.connector.Error as err:
        print(f"Veritabanı hatası: {err}")
        return False
    finally:
        if conn:
            cursor.close()
            conn.close()


def register_user(username, password):
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()
        hashed = hash_password(password)
        query = "INSERT INTO users (username, password) VALUES (%s, %s)"
        cursor.execute(query, (username, hashed))
        conn.commit()
    except mysql.connector.Error as err:
        print(f"Veritabanı hatası: {err}")
    finally:
        if conn:
            cursor.close()
            conn.close()


def user_exists():
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(1) FROM users")
        count = cursor.fetchone()[0]
        return count > 0
    except mysql.connector.Error as err:
        print(f"Veritabanı hatası: {err}")
        return False
    finally:
        if conn:
            cursor.close()
            conn.close()


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            flash('Lütfen önce giriş yapın.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)

    return decorated_function


@app.route('/setup', methods=['GET', 'POST'])
def setup():
    if user_exists():
        return redirect(url_for('login'))  # Kullanıcı varsa giriş sayfasına yönlendir
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        confirm_password = request.form['confirm_password']

        if password != confirm_password:
            flash('Şifreler eşleşmiyor!', 'danger')
            return redirect(url_for('setup'))

        # Kullanıcı adı ve şifrenin minimum gereksinimleri karşılayıp karşılamadığını kontrol edebilirsiniz
        if len(password) < 6:
            flash('Şifreniz en az 6 karakter olmalıdır.', 'danger')
            return redirect(url_for('setup'))

        register_user(username, password)
        flash('Başarıyla kayıt oldunuz! Giriş yapabilirsiniz.', 'success')
        return redirect(url_for('login'))
    return render_template('setup.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if not user_exists():
        return redirect(url_for('setup'))  # Kullanıcı yoksa setup sayfasına yönlendir
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        if validate_login(username, password):
            session['user'] = username
            flash('Başarıyla giriş yaptınız!', 'success')
            return redirect(url_for('index'))
        else:
            flash('Geçersiz kullanıcı adı veya şifre!', 'danger')
            return redirect(url_for('login'))
    return render_template('login.html')


@app.route('/')
@login_required
def index():
    return render_template('index.html',username=session['user'])


@app.route('/data')
@login_required
def data():
    return jsonify(monitor_kafka())


@app.route('/broker-detail')
@login_required
def broker_details_view():
    return render_template('broker.html')


@app.route('/broker-details-data')
@login_required
def broker_details_data():
    broker_id = request.args.get('broker_id')
    broker = next((b for b in brokers if b['broker'] == broker_id), None)
    if broker:
        data = get_broker_data(broker)
        return jsonify(data)
    else:
        return jsonify({"error": "Broker not found"}), 404


def broker_exists(cursor, broker_id):
    cursor.execute("SELECT COUNT(1) FROM broker WHERE id = %s", (broker_id,))
    return cursor.fetchone()[0] > 0


def insert_broker_data(cursor, broker_id, cpu_usage, ram_usage, disk_usage):
    insert_query = """
    INSERT INTO monitor (broker_id, cpu_usage, ram_usage, disk_usage, date)
    VALUES (%s, %s, %s, %s, NOW())
    """
    cursor.execute(insert_query, (broker_id, cpu_usage, ram_usage, disk_usage))


def insert_broker_if_not_exists(cursor, broker_id, host, port):
    cursor.execute("SELECT COUNT(1) FROM broker WHERE id = %s", (broker_id,))
    exists = cursor.fetchone()[0]

    if not exists:
        insert_broker_query = """
        INSERT INTO broker (id, host, port) VALUES (%s, %s, %s)
        """
        cursor.execute(insert_broker_query, (broker_id, host, port))


def monitor_and_store_data():
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()

        while True:
            for broker in brokers:
                broker_data = get_broker_data(broker)
                if broker_data:
                    insert_broker_if_not_exists(cursor, broker_data['broker_id'], broker_data['host'],
                                                broker_data['port'])
                    insert_broker_data(
                        cursor,
                        broker_data['broker_id'],
                        broker_data['cpu_use'],
                        broker_data['memory_usage_percent'],
                        broker_data['disk_usage']
                    )
                    conn.commit()

            time.sleep(300)

    except mysql.connector.Error as err:
        print(f"Veritabanı hatası: {err}")
    finally:
        if conn:
            cursor.close()
            conn.close()


@app.route('/logout')
def logout():
    session.pop('user', None)
    flash('Başarıyla çıkış yaptınız.', 'success')
    return redirect(url_for('login'))


if __name__ == "__main__":
    # Thread for monitoring and storing data every 5 minutes
    monitor_thread = threading.Thread(target=monitor_and_store_data)
    monitor_thread.start()

    # Run Flask app
    app.run(host='0.0.0.0', port=8080, debug=True)
