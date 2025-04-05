from flask import Flask, render_template, request, redirect, url_for
import imaplib
import email
from email.header import decode_header
import re
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from datetime import datetime, timedelta
import os
import threading
import time
import requests

app = Flask(__name__)
app.secret_key = 'your_secret_key_here'

# Cấu hình Google Calendar API
SCOPES = ['https://www.googleapis.com/auth/calendar']
CREDENTIALS_FILE = 'credentials.json'
TOKEN_FILE = 'token.json'

# Cấu hình OpenRouter API
OPENROUTER_API_KEY = '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'  # Thay bằng API Key từ OpenRouter
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"

# Biến toàn cục
email_credentials = {"email": None, "password": None}
planned_tasks = []

# Hàm kết nối và đọc email
def get_emails(email_user, email_pass, imap_server="imap.gmail.com"):
    try:
        print(f"[{datetime.now()}] Đang kết nối tới {imap_server} với {email_user}")
        mail = imaplib.IMAP4_SSL(imap_server)
        mail.login(email_user, email_pass)
        print(f"[{datetime.now()}] Đăng nhập email thành công!")
        mail.select("inbox")
        
        status, messages = mail.search(None, 'UNSEEN')
        if status != 'OK':
            print(f"[{datetime.now()}] Không thể tìm email chưa đọc.")
            mail.logout()
            return []
        
        mail_ids = messages[0].split()
        if not mail_ids:
            print(f"[{datetime.now()}] Không có email chưa đọc trong hộp thư.")
            mail.logout()
            return []
        
        print(f"[{datetime.now()}] Tìm thấy {len(mail_ids)} email chưa đọc.")
        tasks = []
        for mail_id in mail_ids[-5:]:
            status, msg_data = mail.fetch(mail_id, '(RFC822)')
            if status != 'OK':
                print(f"[{datetime.now()}] Không thể đọc email ID: {mail_id}")
                continue
            
            raw_email = msg_data[0][1]
            msg = email.message_from_bytes(raw_email)
            
            subject_raw = decode_header(msg["Subject"])[0][0]
            if isinstance(subject_raw, bytes):
                try:
                    subject = subject_raw.decode('utf-8')
                except UnicodeDecodeError:
                    subject = subject_raw.decode('iso-8859-1', errors='replace')
            else:
                subject = subject_raw
                
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        body_bytes = part.get_payload(decode=True)
                        try:
                            body = body_bytes.decode('utf-8')
                        except UnicodeDecodeError:
                            body = body_bytes.decode('iso-8859-1', errors='replace')
                        break
            else:
                body_bytes = msg.get_payload(decode=True)
                try:
                    body = body_bytes.decode('utf-8')
                except UnicodeDecodeError:
                    body = body_bytes.decode('iso-8859-1', errors='replace')
                
            print(f"[{datetime.now()}] Đã đọc email - Tiêu đề: {subject}")
            print(f"[{datetime.now()}] Nội dung: {body[:100]}...")
            task = analyze_email(subject, body)
            if task:
                print(f"[{datetime.now()}] Task hợp lệ: {task['title']} - Hạn: {task['deadline']}")
                tasks.append(task)
                
        mail.logout()
        print(f"[{datetime.now()}] Tìm thấy {len(tasks)} task từ email.")
        return tasks
    except Exception as e:
        print(f"[{datetime.now()}] Lỗi khi đọc email: {str(e)}")
        return []

# Hàm phân tích email
def analyze_email(subject, body):
    task = {"title": subject, "deadline": None, "description": body}
    deadline_match = re.search(r'due (\d{4}-\d{2}-\d{2})', body, re.IGNORECASE)
    if deadline_match:
        task["deadline"] = deadline_match.group(1)
    return task if task["deadline"] else None

# Hàm gọi OpenRouter API
def generate_detailed_plan_with_openrouter(task):
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }
    prompt = f"""
    Bạn là một trợ lý AI chuyên lập kế hoạch. Dựa trên thông tin sau từ một email, hãy tạo một kế hoạch chi tiết và giải pháp:
    - Tiêu đề: {task['title']}
    - Nội dung: {task['description']}
    - Hạn chót: {task['deadline']}
    Hãy cung cấp:
    1. Kế hoạch chi tiết (các bước thực hiện, thời gian ước tính cho từng bước).
    2. Giải pháp cụ thể để hoàn thành nhiệm vụ.
    """
    data = {
        "model": "mistralai/mixtral-8x7b-instruct:free",
        "messages": [
            {"role": "system", "content": "Bạn là một trợ lý AI chuyên lập kế hoạch."},
            {"role": "user", "content": prompt}
        ],
        "max_tokens": 1000
    }
    
    response = requests.post(OPENROUTER_API_URL, headers=headers, json=data)
    if response.status_code == 200:
        return response.json()["choices"][0]["message"]["content"]
    else:
        print(f"[{datetime.now()}] Lỗi OpenRouter API: {response.status_code} - {response.text}")
        return "Không thể tạo kế hoạch do lỗi API."

# Hàm AI lập kế hoạch và giải pháp
def ai_plan_and_solve(tasks):
    current_date = datetime.now()
    planned_tasks = []
    
    for task in tasks:
        deadline = datetime.strptime(task["deadline"], "%Y-%m-%d")
        days_until_deadline = (deadline - current_date).days
        
        ai_plan = generate_detailed_plan_with_openrouter(task)
        print(f"[{datetime.now()}] Kế hoạch từ OpenRouter:\n{ai_plan}")
        
        complexity = len(task["description"].split()) // 10
        estimated_hours = min(max(complexity, 1), 5)
        
        if days_until_deadline > 0:
            start_date = current_date + timedelta(days=1)
            hours_per_day = estimated_hours / days_until_deadline
            plan = {
                "title": task["title"],
                "deadline": task["deadline"],
                "estimated_hours": estimated_hours,
                "start_date": start_date.strftime("%Y-%m-%d"),
                "daily_hours": round(hours_per_day, 1),
                "priority": "Cao" if days_until_deadline < 3 else "Trung bình",
                "solution": ai_plan
            }
            planned_tasks.append(plan)
    
    return planned_tasks

# Hàm thiết lập Google Calendar API
def get_calendar_service():
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
        creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, 'w') as token:
            token.write(creds.to_json())
    return build('calendar', 'v3', credentials=creds)

# Hàm thêm sự kiện vào Google Calendar
def add_to_calendar(service, plan):
    start_date = datetime.strptime(plan["start_date"], "%Y-%m-%d")
    end_date = start_date + timedelta(hours=plan["daily_hours"])
    
    event = {
        'summary': f"{plan['title']} (Ưu tiên: {plan['priority']})",
        'description': f"Cần {plan['estimated_hours']} giờ, làm {plan['daily_hours']} giờ/ngày\n{plan['solution']}",
        'start': {
            'dateTime': start_date.isoformat(),
            'timeZone': 'Asia/Ho_Chi_Minh',
        },
        'end': {
            'dateTime': end_date.isoformat(),
            'timeZone': 'Asia/Ho_Chi_Minh',
        },
        'reminders': {
            'useDefault': False,
            'overrides': [{'method': 'popup', 'minutes': 30}],
        },
    }
    service.events().insert(calendarId='primary', body=event).execute()
    print(f"[{datetime.now()}] Đã thêm sự kiện: {plan['title']} vào Google Calendar")

# Hàm chạy nền để kiểm tra email
def check_emails_periodically():
    global planned_tasks
    while True:
        if not email_credentials["email"] or not email_credentials["password"]:
            print(f"[{datetime.now()}] Chưa đăng nhập. Đang chờ...")
            time.sleep(60)
            continue
        
        try:
            service = get_calendar_service()
            tasks = get_emails(email_credentials["email"], email_credentials["password"])
            if tasks:
                print(f"[{datetime.now()}] Tìm thấy {len(tasks)} email mới.")
                new_planned_tasks = ai_plan_and_solve(tasks)
                if new_planned_tasks:
                    planned_tasks = new_planned_tasks
                    for plan in planned_tasks:
                        add_to_calendar(service, plan)
            else:
                print(f"[{datetime.now()}] Không có email mới.")
        except Exception as e:
            print(f"[{datetime.now()}] Lỗi trong quá trình kiểm tra email: {str(e)}")
        
        time.sleep(60)

# Routes
@app.route('/')
def index():
    if email_credentials["email"]:
        return redirect(url_for('dashboard'))
    return render_template('index.html', error=None)

@app.route('/login', methods=['POST'])
def login():
    email_user = request.form['email']
    email_pass = request.form['password']
    
    try:
        tasks = get_emails(email_user, email_pass)
        email_credentials["email"] = email_user
        email_credentials["password"] = email_pass
        
        if not any(t.name == 'email_thread' for t in threading.enumerate()):
            email_thread = threading.Thread(target=check_emails_periodically, name='email_thread', daemon=True)
            email_thread.start()
        
        return redirect(url_for('dashboard'))
    except Exception as e:
        return render_template('index.html', error=str(e))

@app.route('/dashboard')
def dashboard():
    if not email_credentials["email"]:
        return redirect(url_for('index'))
    return render_template('dashboard.html', tasks=planned_tasks)

# Template HTML không đổi
index_html = """
<!DOCTYPE html>
<html>
<head><title>Email Task Bot</title></head>
<body>
    <h1>Đăng nhập Email</h1>
    {% if error %}
        <p style="color: red;">{{ error }}</p>
    {% endif %}
    <form method="post" action="/login">
        <input type="email" name="email" placeholder="Email" required><br>
        <input type="password" name="password" placeholder="Mật khẩu" required><br>
        <input type="submit" value="Đăng nhập">
    </form>
</body>
</html>
"""

dashboard_html = """
<!DOCTYPE html>
<html>
<head>
    <title>Dashboard</title>
    <meta http-equiv="refresh" content="60">
</head>
<body>
    <h1>Kế hoạch và Giải pháp</h1>
    {% if tasks %}
        <ul>
        {% for task in tasks %}
            <li>
                <strong>{{ task.title }}</strong><br>
                - Hạn: {{ task.deadline }}<br>
                - Bắt đầu: {{ task.start_date }}<br>
                - Ước tính: {{ task.estimated_hours }} giờ<br>
                - Mỗi ngày: {{ task.daily_hours }} giờ<br>
                - Ưu tiên: {{ task.priority }}<br>
                - <strong>Giải pháp:</strong> {{ task.solution }}
            </li>
        {% endfor %}
        </ul>
        <p>Đã thêm vào Google Calendar với nhắc nhở!</p>
    {% else %}
        <p>Không có email mới hoặc task hợp lệ. Gửi email với "due YYYY-MM-DD" để thử.</p>
    {% endif %}
</body>
</html>
"""

if not os.path.exists('templates'):
    os.makedirs('templates')
with open('templates/index.html', 'w', encoding='utf-8') as f:
    f.write(index_html)
with open('templates/dashboard.html', 'w', encoding='utf-8') as f:
    f.write(dashboard_html)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))