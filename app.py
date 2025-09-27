from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, get_flashed_messages
from itsdangerous import URLSafeSerializer, BadSignature
import os, smtplib, imaplib, email, re, threading, time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from database import db  # <-- import the singleton db
from models import User, Booking, Venue

app = Flask(__name__)
app.secret_key = "dev-secret-key"

# Ensure instance folder exists
instance_path = os.path.join(os.path.abspath(os.path.dirname(__file__)), "instance")
os.makedirs(instance_path, exist_ok=True)

# DB config
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{os.path.join(instance_path, 'venue_booking.db')}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# Initialize db with the app
db.init_app(app)
# SMTP / Email settings (fill these for real email sending)
app.config.setdefault("MAIL_SERVER", os.environ.get("MAIL_SERVER", "smtp.gmail.com"))
app.config.setdefault("MAIL_PORT", int(os.environ.get("MAIL_PORT", 587)))
app.config.setdefault("MAIL_USERNAME", os.environ.get("MAIL_USERNAME", "st.francis.college.help@gmail.com"))
app.config.setdefault("MAIL_PASSWORD", os.environ.get("MAIL_PASSWORD", "gpoyezwqzkztcvoy"))
app.config.setdefault("MAIL_USE_TLS", os.environ.get("MAIL_USE_TLS", "True") in [True, "True", "true", "1"]) 
app.config.setdefault("ADMIN_EMAIL", os.environ.get("ADMIN_EMAIL", "kannadagamer387@gmail.com"))
app.config.setdefault("BASE_URL", os.environ.get("BASE_URL", "http://127.0.0.1:5000"))
app.config.setdefault("IMAP_SERVER", os.environ.get("IMAP_SERVER", "imap.gmail.com"))
app.config.setdefault("IMAP_PORT", int(os.environ.get("IMAP_PORT", 993)))

serializer = URLSafeSerializer(app.secret_key, salt="venue-booking-email-actions")

def generate_decision_token(booking_id: int, action: str) -> str:
    return serializer.dumps({"booking_id": booking_id, "action": action})

def verify_decision_token(token: str):
    try:
        data = serializer.loads(token)
        return data
    except BadSignature:
        return None

def send_email(subject: str, html_body: str, to_email: str) -> bool:
    if not app.config["MAIL_SERVER"] or not app.config["MAIL_USERNAME"] or not app.config["MAIL_PASSWORD"]:
        print("[EMAIL] Missing SMTP config; printing email instead:\nSUBJECT:", subject, "\nTO:", to_email, "\nBODY:\n", html_body)
        return False
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = app.config["MAIL_USERNAME"]
    msg["To"] = to_email
    msg.attach(MIMEText(html_body, "html"))
    try:
        with smtplib.SMTP(app.config["MAIL_SERVER"], app.config["MAIL_PORT"]) as server:
            if app.config.get("MAIL_USE_TLS", True):
                server.starttls()
            server.login(app.config["MAIL_USERNAME"], app.config["MAIL_PASSWORD"])
            server.sendmail(msg["From"], [msg["To"]], msg.as_string())
        return True
    except Exception as e:
        print("[EMAIL] Send failed:", e)
        return False

def send_booking_email_to_admin(booking: Booking):
    approve_token = generate_decision_token(booking.id, "approve")
    reject_token = generate_decision_token(booking.id, "reject")
    base = app.config.get("BASE_URL") or "http://127.0.0.1:5000"
    approve_link = f"{base}/email/booking/{approve_token}"
    reject_link = f"{base}/email/booking/{reject_token}"
    
    # Get all bookings for the same event (same faculty, venue, date, event_name)
    related_bookings = Booking.query.filter_by(
        faculty_name=booking.faculty_name,
        venue=booking.venue,
        date=booking.date,
        event_name=booking.event_name,
        status="Pending"
    ).all()
    
    slots = [b.slot for b in related_bookings]
    slots_text = ", ".join(slots) if len(slots) > 1 else booking.slot
    
    subject = f"Booking #{booking.id} Pending Approval"
    html = f"""
    <div style='font-family:Arial,sans-serif'>
      <h2>New Booking Pending Approval</h2>
      <p><strong>ID:</strong> {booking.id}</p>
      <p><strong>Event:</strong> {booking.event_name}</p>
      <p><strong>Faculty:</strong> {booking.faculty_name}</p>
      <p><strong>Venue:</strong> {booking.venue}</p>
      <p><strong>Date:</strong> {booking.date}</p>
      <p><strong>Slot{'s' if len(slots) > 1 else ''}:</strong> {slots_text}</p>
      <p><strong>People:</strong> {booking.num_people}</p>
      {f"<p><strong>Canteen Requirements:</strong> {booking.canteen_details}</p>" if booking.canteen_details else ""}
      <div style='margin:14px 0;padding:12px;border:1px dashed #bbb;border-radius:8px;background:#fafafa;'>
        <p style='margin:0 0 6px 0;'><strong>How to decide (no browser needed):</strong></p>
        <ol style='margin:0 0 6px 18px;'>
          <li>Reply to this email with the single word <strong>APPROVE</strong> to approve.</li>
          <li>Reply to this email with the single word <strong>REJECT</strong> to reject.</li>
        </ol>
        <p style='margin:0;color:#666;font-size:12px'>We'll process your reply automatically within a few seconds.</p>
      </div>
      <p style='color:#999;font-size:11px;margin-top:10px'>Advanced: If you prefer a link and your server is accessible, you can open: 
      <br/>Approve: {approve_link}
      <br/>Reject: {reject_link}
      </p>
    </div>
    """
    send_email(subject, html, app.config["ADMIN_EMAIL"])


# Background IMAP processor: handle reply-based approvals
RE_BOOKING_ID = re.compile(r"Booking\s*#(\d+)")

def process_email_replies_once():
    try:
        imap_server = app.config["IMAP_SERVER"]
        imap_port = app.config["IMAP_PORT"]
        username = app.config["MAIL_USERNAME"]
        password = app.config["MAIL_PASSWORD"]
        if not imap_server or not username or not password:
            return
        with imaplib.IMAP4_SSL(imap_server, imap_port) as M:
            M.login(username, password)
            M.select('INBOX')
            typ, data = M.search(None, '(UNSEEN)')
            if typ != 'OK':
                return
            for num in data[0].split():
                typ, msg_data = M.fetch(num, '(RFC822)')
                if typ != 'OK':
                    continue
                msg = email.message_from_bytes(msg_data[0][1])
                subject = msg.get('Subject', '')
                match = RE_BOOKING_ID.search(subject)
                body_text = ''
                if msg.is_multipart():
                    for part in msg.walk():
                        ctype = part.get_content_type()
                        if ctype == 'text/plain' and part.get_content_disposition() is None:
                            try:
                                body_text = part.get_payload(decode=True).decode(part.get_content_charset() or 'utf-8', errors='ignore')
                                break
                            except Exception:
                                pass
                else:
                    try:
                        body_text = msg.get_payload(decode=True).decode(msg.get_content_charset() or 'utf-8', errors='ignore')
                    except Exception:
                        pass
                action = None
                lower_body = body_text.lower()
                if 'approve' in lower_body:
                    action = 'approve'
                elif 'reject' in lower_body:
                    action = 'reject'
                if match and action:
                    booking_id = int(match.group(1))
                    booking = Booking.query.get(booking_id)
                    if booking:
                        # Find all related bookings (same faculty, venue, date, event_name)
                        related_bookings = Booking.query.filter_by(
                            faculty_name=booking.faculty_name,
                            venue=booking.venue,
                            date=booking.date,
                            event_name=booking.event_name,
                            status="Pending"
                        ).all()
                        
                        for related_booking in related_bookings:
                            related_booking.status = 'Approved' if action == 'approve' else 'Rejected'
                        db.session.commit()
                # mark as seen regardless
                M.store(num, '+FLAGS', '\\Seen')
            M.logout()
    except Exception as e:
        print('[IMAP] Processing error:', e)


def start_imap_background_worker():
    def loop():
        while True:
            with app.app_context():
                process_email_replies_once()
            time.sleep(60)
    t = threading.Thread(target=loop, daemon=True)
    t.start()

# Initialize DB and create default admin
# Initialize DB and create default users
with app.app_context():
    db.create_all()
    
    # Seed default venues if none exist
    if Venue.query.count() == 0:
        default_venues = [
            Venue(name="Auditorium", capacity=400, location="Main Block"),
            Venue(name="Conference Room", capacity=60, location="Admin Block"),
            Venue(name="Lab 1", capacity=40, location="CS Dept"),
            Venue(name="Lab 2", capacity=40, location="CS Dept"),
        ]
        db.session.add_all(default_venues)
        print("Seeded default venues")

    # Default admin
    if not User.query.filter_by(username="admin").first():
        admin = User(username="admin", password="admin123", role="admin")
        db.session.add(admin)
        print("Default admin created: username=admin / password=admin123")
    
    # Dummy faculty
    if not User.query.filter_by(username="faculty").first():
        faculty = User(username="faculty", password="faculty123", role="faculty")
        db.session.add(faculty)
        print("Dummy faculty created: username=faculty / password=faculty123")
    
    # Commit all changes
    db.session.commit()

# Home page
@app.route("/")
def home():
    return render_template("home.html", title="Home")

# Login selection
@app.route("/login")
def login():
    # Clear any existing flash messages when visiting the page
    get_flashed_messages()
    return render_template("login.html", title="Login")

# Admin login
@app.route("/login/admin", methods=["GET", "POST"])
def login_admin():
    # Clear any existing flash messages when visiting the page
    if request.method == "GET":
        # Clear flash messages by consuming them
        get_flashed_messages()
    
    if request.method == "POST":
        username = request.form.get("username").strip()
        password = request.form.get("password").strip()
        user = User.query.filter_by(username=username, password=password, role="admin").first()
        if user:
            session["user"] = {"id": user.id, "username": user.username, "role": user.role}
            flash("Admin login successful", "success")
            return redirect(url_for("admin_dashboard"))
        else:
            flash("Invalid admin credentials", "danger")
    return render_template("login_admin.html", title="Admin Login")

# Faculty login
@app.route("/login/faculty", methods=["GET", "POST"])
def login_faculty():
    # Clear any existing flash messages when visiting the page
    if request.method == "GET":
        # Clear flash messages by consuming them
        get_flashed_messages()
    
    if request.method == "POST":
        username = request.form.get("username").strip()
        password = request.form.get("password").strip()
        user = User.query.filter_by(username=username, password=password, role="faculty").first()
        if user:
            session["user"] = {"id": user.id, "username": user.username, "role": user.role}
            flash("Faculty login successful", "success")
            return redirect(url_for("faculty_dashboard"))
        else:
            flash("Invalid faculty credentials", "danger")
    return render_template("login_faculty.html", title="Faculty Login")

# Admin Dashboard
@app.route("/admin")
def admin_dashboard():
    if session.get("user") and session["user"]["role"] == "admin":
        bookings = Booking.query.order_by(Booking.date.desc()).all()
        
        # Group bookings by event (same faculty, venue, date, event_name)
        grouped_bookings = []
        processed_ids = set()
        
        for booking in bookings:
            if booking.id in processed_ids:
                continue
                
            # Find all related bookings
            related_bookings = Booking.query.filter_by(
                faculty_name=booking.faculty_name,
                venue=booking.venue,
                date=booking.date,
                event_name=booking.event_name
            ).order_by(Booking.slot.asc()).all()
            
            
            # Create grouped booking object
            grouped_booking = {
                'primary_id': booking.id,
                'event_name': booking.event_name,
                'faculty_name': booking.faculty_name,
                'venue': booking.venue,
                'date': booking.date,
                'num_people': booking.num_people,
                'status': booking.status,
                'canteen_details': booking.canteen_details,
                'slots': [b.slot for b in related_bookings]
            }
            
            grouped_bookings.append(grouped_booking)
            
            # Mark all related bookings as processed
            for related in related_bookings:
                processed_ids.add(related.id)
        
        stats = {
            "total_bookings": Booking.query.count(),
            "pending": Booking.query.filter_by(status="Pending").count(),
            "approved": Booking.query.filter_by(status="Approved").count(),
            "rejected": Booking.query.filter_by(status="Rejected").count(),
            "venues": Venue.query.count(),
            "faculty": User.query.filter_by(role="faculty").count(),
        }
        venues = [v.name for v in Venue.query.order_by(Venue.name.asc()).all()]
        return render_template("admin_dashboard.html", title="Admin Dashboard", grouped_bookings=grouped_bookings, stats=stats, venues=venues)
    flash("Access denied", "danger")
    return redirect(url_for("login"))

# Admin: Get detailed slot information for calendar
@app.route("/admin/slot_details", methods=["POST"])
def admin_slot_details():
    if session.get("user") and session["user"]["role"] == "admin":
        venue = request.form.get("venue")
        date = request.form.get("date")
        if not venue or not date:
            return jsonify({"booked": [], "pending": []})
        
        bookings = Booking.query.filter_by(venue=venue, date=date).all()
        
        # Separate approved and pending bookings with full details
        booked_bookings = []
        pending_bookings = []
        
        for booking in bookings:
            booking_data = {
                'id': booking.id,
                'slot': booking.slot,
                'event_name': booking.event_name,
                'faculty_name': booking.faculty_name,
                'num_people': booking.num_people,
                'canteen_details': booking.canteen_details,
                'status': booking.status
            }
            
            if booking.status == "Approved":
                booked_bookings.append(booking_data)
            elif booking.status == "Pending":
                pending_bookings.append(booking_data)
        
        return jsonify({
            "booked": booked_bookings,
            "pending": pending_bookings
        })
    flash("Access denied", "danger")
    return redirect(url_for("login"))

# Faculty Dashboard
# Faculty dashboard
@app.route("/faculty")
def faculty_dashboard():
    if session.get("user") and session["user"]["role"] == "faculty":
        venues = [v.name for v in Venue.query.order_by(Venue.name.asc()).all()]
        return render_template("faculty_dashboard.html", title="Faculty Dashboard", venues=venues)
    flash("Access denied", "danger")
    return redirect(url_for("login_faculty"))

# Fetch booked slots for a venue & date (AJAX)
@app.route("/faculty/booked_slots", methods=["POST"])
def booked_slots():
    venue = request.form.get("venue")
    date = request.form.get("date")
    if not venue or not date:
        return jsonify({"booked": [], "pending": []})
    
    # 1. Fetch ALL relevant bookings
    bookings = Booking.query.filter_by(venue=venue, date=date).all()
    
    # 2. Separate approved and pending slots using case-insensitive or explicit checks
    
    # Check for APPROVED status (Handles 'Approved', 'approved', 'APPROVED')
    booked_slots = [
        b.slot for b in bookings 
        if b.status.lower() == "approved"
    ]
    
    # Check for PENDING status (Uses the exact status set during submission)
    pending_slots = [
        b.slot for b in bookings 
        if b.status == "Pending"
    ]
    
    # Ensure you also check for other statuses that should block booking, 
    # such as 'CONFIRMED' if that is used. If so, add it to the booked_slots list:
    # booked_slots.extend([b.slot for b in bookings if b.status.lower() == "confirmed"])

    print(f"DEBUG: Booked Slots: {booked_slots}, Pending Slots: {pending_slots}")
    
    return jsonify({
        "booked": booked_slots,
        "pending": pending_slots
    }) 
# Submit new booking
@app.route("/faculty/book", methods=["POST"])
def faculty_book():
    if session.get("user") and session["user"]["role"] == "faculty":
        data = request.form
        slots_str = data.get("slots", "")
        slots = [slot.strip() for slot in slots_str.split(",") if slot.strip()]
        
        if not slots:
            flash("Please select at least one time slot", "danger")
            return redirect(url_for("faculty_dashboard"))
        
        # Create a booking for each selected slot
        booking_list = []

        for slot in slots:
            booking = Booking(
                event_name = data.get("event_name"),
                faculty_name = session["user"]["username"],
                num_people = int(data.get("num_people") or 0),
                venue = data.get("venue"),
                slot = slot,
                date = data.get("date"),
                status = "Pending",
                canteen_details = (data.get("canteen_details") or None) if data.get("canteen_required") else None,
                other_requirements = (data.get("other_requirements") or None)
            )
            db.session.add(booking)
            booking_list.append(booking)

        try:
            db.session.commit()  # Commit all bookings
        except Exception as e:
            db.session.rollback()
            flash(f"Error saving booking: {e}", "danger")
            return redirect(url_for("faculty_dashboard"))

        # Send email in background thread to prevent blocking Render worker
        if booking_list:
            first_booking = booking_list[0]
            threading.Thread(target=send_booking_email_to_admin, args=(first_booking,), daemon=True).start()

        booking_ids = [b.id for b in booking_list]
        return redirect(url_for("booking_submitted", booking_id=booking_ids[0]))

    flash("Access denied", "danger")
    return redirect(url_for("login_faculty"))

# Faculty: Submission success page
@app.route("/faculty/booking_submitted/<int:booking_id>")
def booking_submitted(booking_id):
    if session.get("user") and session["user"]["role"] == "faculty":
        booking = Booking.query.get_or_404(booking_id)
        if booking.faculty_name != session["user"]["username"]:
            flash("Access denied", "danger")
            return redirect(url_for("faculty_dashboard"))
        return render_template("faculty_booking_submitted.html", title="Booking Submitted", booking=booking)
    flash("Access denied", "danger")
    return redirect(url_for("login_faculty"))

# Email approval endpoint (no login required)

# Faculty: My Bookings
@app.route("/faculty/my_bookings")
def faculty_my_bookings():
    if session.get("user") and session["user"]["role"] == "faculty":
        my_name = session["user"]["username"]
        my_bookings = Booking.query.filter_by(faculty_name=my_name).order_by(Booking.date.desc()).all()
        return render_template("faculty_my_bookings.html", title="My Bookings", bookings=my_bookings)
    flash("Access denied", "danger")
    return redirect(url_for("login_faculty"))

@app.route("/faculty/cancel/<int:booking_id>", methods=["POST"]) 
def faculty_cancel_booking(booking_id):
    if session.get("user") and session["user"]["role"] == "faculty":
        booking = Booking.query.get_or_404(booking_id)
        if booking.faculty_name != session["user"]["username"]:
            flash("You can only cancel your own bookings", "danger")
            return redirect(url_for("faculty_my_bookings"))
        if booking.status == "Approved" or booking.status == "Pending":
            db.session.delete(booking)
            db.session.commit()
            flash("Booking cancelled", "info")
        return redirect(url_for("faculty_my_bookings"))
    flash("Access denied", "danger")
    return redirect(url_for("login_faculty"))

# Admin: Approve/Reject
@app.route("/admin/approve/<int:booking_id>", methods=["POST"]) 
def admin_approve(booking_id):
    if session.get("user") and session["user"]["role"] == "admin":
        booking = Booking.query.get_or_404(booking_id)
        
        # Find all related bookings (same faculty, venue, date, event_name)
        related_bookings = Booking.query.filter_by(
            faculty_name=booking.faculty_name,
            venue=booking.venue,
            date=booking.date,
            event_name=booking.event_name,
            status="Pending"
        ).all()
        
        # Approve all related bookings
        for related_booking in related_bookings:
            related_booking.status = "Approved"
        
        db.session.commit()
        
        slots_text = ", ".join([b.slot for b in related_bookings])
        flash(f"Booking approved for slots: {slots_text}", "success")
        return redirect(url_for("admin_dashboard"))
    flash("Access denied", "danger")
    return redirect(url_for("login"))

@app.route("/admin/reject/<int:booking_id>", methods=["POST"]) 
def admin_reject(booking_id):
    if session.get("user") and session["user"]["role"] == "admin":
        booking = Booking.query.get_or_404(booking_id)
        
        # Find all related bookings (same faculty, venue, date, event_name)
        related_bookings = Booking.query.filter_by(
            faculty_name=booking.faculty_name,
            venue=booking.venue,
            date=booking.date,
            event_name=booking.event_name,
            status="Pending"
        ).all()
        
        # Reject all related bookings
        for related_booking in related_bookings:
            related_booking.status = "Rejected"
        
        db.session.commit()
        
        slots_text = ", ".join([b.slot for b in related_bookings])
        flash(f"Booking rejected for slots: {slots_text}", "info")
        return redirect(url_for("admin_dashboard"))
    flash("Access denied", "danger")
    return redirect(url_for("login"))

# Admin: Clear all booking history
@app.route("/admin/clear_history", methods=["POST"]) 
def admin_clear_history():
    if session.get("user") and session["user"]["role"] == "admin":
        # Delete all bookings
        Booking.query.delete()
        db.session.commit()
        flash("All booking history cleared", "info")
        return redirect(url_for("admin_dashboard"))
    flash("Access denied", "danger")
    return redirect(url_for("login"))

# Admin: Venues management
@app.route("/admin/venues")
def admin_venues():
    if session.get("user") and session["user"]["role"] == "admin":
        venues = Venue.query.order_by(Venue.name.asc()).all()
        return render_template("admin_venues.html", title="Manage Venues", venues=venues)
    flash("Access denied", "danger")
    return redirect(url_for("login"))

@app.route("/admin/venues/add", methods=["POST"]) 
def admin_add_venue():
    if session.get("user") and session["user"]["role"] == "admin":
        name = request.form.get("name").strip()
        capacity = int(request.form.get("capacity") or 0)
        location = request.form.get("location", "").strip()
        if not name or capacity <= 0:
            flash("Name and positive capacity required", "danger")
            return redirect(url_for("admin_venues"))
        if Venue.query.filter_by(name=name).first():
            flash("Venue already exists", "danger")
            return redirect(url_for("admin_venues"))
        db.session.add(Venue(name=name, capacity=capacity, location=location))
        db.session.commit()
        flash("Venue added", "success")
        return redirect(url_for("admin_venues"))
    flash("Access denied", "danger")
    return redirect(url_for("login"))

@app.route("/admin/venues/delete/<int:venue_id>", methods=["POST"]) 
def admin_delete_venue(venue_id):
    if session.get("user") and session["user"]["role"] == "admin":
        venue = Venue.query.get_or_404(venue_id)
        # Optional: check no future bookings exist for this venue
        db.session.delete(venue)
        db.session.commit()
        flash("Venue deleted", "info")
        return redirect(url_for("admin_venues"))
    flash("Access denied", "danger")
    return redirect(url_for("login"))

# Admin: Faculty management
@app.route("/admin/faculty")
def admin_faculty():
    if session.get("user") and session["user"]["role"] == "admin":
        faculty_users = User.query.filter_by(role="faculty").order_by(User.username.asc()).all()
        return render_template("admin_faculty.html", title="Manage Faculty", faculty_users=faculty_users)
    flash("Access denied", "danger")
    return redirect(url_for("login"))

@app.route("/admin/faculty/add", methods=["POST"]) 
def admin_add_faculty():
    if session.get("user") and session["user"]["role"] == "admin":
        username = (request.form.get("username") or "").strip()
        password = (request.form.get("password") or "").strip()
        if not username or not password:
            flash("Username and password required", "danger")
            return redirect(url_for("admin_faculty"))
        if User.query.filter_by(username=username).first():
            flash("Username already exists", "danger")
            return redirect(url_for("admin_faculty"))
        db.session.add(User(username=username, password=password, role="faculty"))
        db.session.commit()
        flash("Faculty added", "success")
        return redirect(url_for("admin_faculty"))
    flash("Access denied", "danger")
    return redirect(url_for("login"))

@app.route("/admin/faculty/delete/<int:user_id>", methods=["POST"]) 
def admin_delete_faculty(user_id):
    if session.get("user") and session["user"]["role"] == "admin":
        user = User.query.get_or_404(user_id)
        if user.role != "faculty":
            flash("Cannot delete non-faculty user", "danger")
            return redirect(url_for("admin_faculty"))
        db.session.delete(user)
        db.session.commit()
        flash("Faculty deleted", "info")
        return redirect(url_for("admin_faculty"))
    flash("Access denied", "danger")
    return redirect(url_for("login"))

@app.route("/admin/faculty/reset/<int:user_id>", methods=["POST"]) 
def admin_reset_faculty_password(user_id):
    if session.get("user") and session["user"]["role"] == "admin":
        user = User.query.get_or_404(user_id)
        if user.role != "faculty":
            flash("Cannot reset non-faculty user", "danger")
            return redirect(url_for("admin_faculty"))
        new_password = (request.form.get("new_password") or "").strip()
        if not new_password:
            flash("New password required", "danger")
            return redirect(url_for("admin_faculty"))
        user.password = new_password
        db.session.commit()
        flash("Password reset", "success")
        return redirect(url_for("admin_faculty"))
    flash("Access denied", "danger")
    return redirect(url_for("login"))

# Logout
@app.route("/logout")
def logout():
    session.pop("user", None)
    flash("Logged out successfully", "info")
    return redirect(url_for("home"))


if __name__ == "__main__":
    # Do NOT start the IMAP background worker on Render
    # start_imap_background_worker()
    app.run(host="0.0.0.0", port=5000, debug=True)
