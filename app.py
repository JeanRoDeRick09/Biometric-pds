from flask import Flask, render_template, request, redirect, url_for, send_file, jsonify
import psycopg2
import qrcode
import io
import base64
import serial
import time
import adafruit_fingerprint

app = Flask(__name__)
enroll_message = ""

# ✅ Supabase PostgreSQL connection
conn = psycopg2.connect(
    host="aws-0-ap-south-1.pooler.supabase.com",
    dbname="postgres",
    user="postgres.awogngrlvcnjovpiykvy",
    password="Djean@2909",
    port=5432,
    connect_timeout=10,
    options='-c statement_timeout=10000'
)
cursor = conn.cursor()

# ✅ Fingerprint sensor setup
def get_fingerprint_sensor():
    try:
        uart = serial.Serial("/dev/ttyUSB0", baudrate=57600, timeout=1)
        return adafruit_fingerprint.Adafruit_Fingerprint(uart)
    except Exception as e:
        print("Fingerprint sensor error:", e)
        return None

finger = get_fingerprint_sensor()

# ✅ Helper: Get next available fingerprint ID
def get_next_fingerprint_id():
    cursor.execute("SELECT fingerprint_id FROM members WHERE fingerprint_id IS NOT NULL ORDER BY fingerprint_id")
    used_ids = [row[0] for row in cursor.fetchall()]
    fid = 1
    while fid in used_ids:
        fid += 1
    return fid

@app.route('/')
def home():
    return render_template('index.html')

# ✅ Create family + members (no fingerprint yet)
@app.route('/create', methods=['GET', 'POST'])
def create_family():
    if request.method == 'POST':
        head = request.form['head']
        head_age = int(request.form['head_age'])
        spouse = request.form['spouse']
        spouse_age = int(request.form['spouse_age'])
        spouse_gender = request.form['spouse_gender']
        no_of_children = int(request.form['no_of_children'])

        cursor.execute("INSERT INTO family (head, spouse, no_of_children) VALUES (%s, %s, %s) RETURNING id",
                       (head, spouse, no_of_children))
        family_id = cursor.fetchone()[0]

        cursor.execute("INSERT INTO members (family_id, name, age, gender, fingerprint_id) VALUES (%s, %s, %s, %s, NULL)",
                       (family_id, head, head_age, 'Male'))
        cursor.execute("INSERT INTO members (family_id, name, age, gender, fingerprint_id) VALUES (%s, %s, %s, %s, NULL)",
                       (family_id, spouse, spouse_age, spouse_gender))

        for i in range(no_of_children):
            cname = request.form.get(f'child_name_{i}')
            cage = int(request.form.get(f'child_age_{i}'))
            cgender = request.form.get(f'child_gender_{i}')
            cursor.execute("INSERT INTO members (family_id, name, age, gender, fingerprint_id) VALUES (%s, %s, %s, %s, NULL)",
                           (family_id, cname, cage, cgender))

        conn.commit()
        return redirect(f'/enroll_fingerprints/{family_id}/0')

    return render_template('create_family.html')

# ✅ Enroll each member one-by-one
@app.route('/enroll_fingerprints/<int:family_id>/<int:index>', methods=['GET', 'POST'])
def enroll_fingerprint_page(family_id, index):
    global enroll_message

    cursor.execute("SELECT id, name FROM members WHERE family_id = %s ORDER BY id", (family_id,))
    members = cursor.fetchall()

    if index >= len(members):
        return redirect('/view')

    member_id, name = members[index]

    if request.method == 'POST':
        fid = get_next_fingerprint_id()
        result = enroll_fingerprint(fid, name)
        if result:
            cursor.execute("UPDATE members SET fingerprint_id = %s WHERE id = %s", (fid, member_id))
            conn.commit()
            enroll_message = f"✅ {name} enrolled with ID {fid}"
        else:
            enroll_message = f"❌ Failed to enroll {name}. Try again."

    return render_template('enroll_fingerprints.html',
                           member_name=name,
                           family_id=family_id,
                           index=index)

# ✅ Retry enrollment for a single member
@app.route('/enroll_fingerprints_retry/<int:member_id>', methods=['GET', 'POST'])
def enroll_fingerprint_retry(member_id):
    global enroll_message

    cursor.execute("SELECT name, fingerprint_id FROM members WHERE id = %s", (member_id,))
    result = cursor.fetchone()

    if not result:
        return "Member not found", 404

    name, existing_fid = result

    if request.method == 'POST':
        if existing_fid:
            fid = existing_fid  # reuse same slot
        else:
            fid = get_next_fingerprint_id()

        result = enroll_fingerprint(fid, name)
        if result:
            cursor.execute("UPDATE members SET fingerprint_id = %s WHERE id = %s", (fid, member_id))
            conn.commit()
            enroll_message = f"✅ {name} enrolled with ID {fid}"
            return redirect("/view")
        else:
            enroll_message = f"❌ Failed to enroll {name}. Try again."

    return render_template('retry_fingerprint.html', member_name=name, member_id=member_id)

# ✅ Fingerprint enrollment logic
def enroll_fingerprint(location, label):
    global enroll_message
    if finger is None:
        enroll_message = "⚠️ Fingerprint sensor not connected"
        return None

    try:
        enroll_message = f"👉 {label}: Place finger"
        start_time = time.time()
        while finger.get_image() != adafruit_fingerprint.OK:
            if time.time() - start_time > 15:
                return None

        if finger.image_2_tz(1) != adafruit_fingerprint.OK:
            return None

        enroll_message = f"✋ {label}: Remove finger"
        time.sleep(2)
        while finger.get_image() != adafruit_fingerprint.NOFINGER:
            pass

        enroll_message = f"👉 {label}: Place same finger again"
        start_time = time.time()
        while finger.get_image() != adafruit_fingerprint.OK:
            if time.time() - start_time > 15:
                return None

        if finger.image_2_tz(2) != adafruit_fingerprint.OK:
            return None

        if finger.create_model() != adafruit_fingerprint.OK:
            return None

        if finger.store_model(location) != adafruit_fingerprint.OK:
            return None

        return location

    except Exception as e:
        enroll_message = f"❌ Error: {str(e)}"
        return None

# ✅ Show current fingerprint prompt
@app.route('/enroll_status')
def enroll_status():
    return jsonify({'message': enroll_message})

# ✅ View all families
@app.route('/view')
def view_family():
    cursor.execute("SELECT * FROM family")
    families = cursor.fetchall()

    family_data = []
    for fam in families:
        fam_id = fam[0]
        cursor.execute("SELECT id, name, age, gender, fingerprint_id FROM members WHERE family_id = %s", (fam_id,))
        members = cursor.fetchall()

        qr = qrcode.make(str(fam_id))
        buffer = io.BytesIO()
        qr.save(buffer, format='PNG')
        qr_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')

        family_data.append({
            "id": fam[0],
            "head": fam[1],
            "spouse": fam[2],
            "children": fam[3],
            "members": members,
            "qr": qr_base64
        })

    return render_template('view_family.html', families=family_data)

# ✅ Download QR
@app.route('/download_qr/<int:family_id>')
def download_qr(family_id):
    qr = qrcode.make(str(family_id))
    buffer = io.BytesIO()
    qr.save(buffer, format='PNG')
    buffer.seek(0)
    return send_file(buffer, mimetype='image/png', as_attachment=True, download_name=f'family_{family_id}_qr.png')

# ✅ Delete a family
@app.route('/delete/<int:family_id>')
def delete_family(family_id):
    cursor.execute("DELETE FROM family WHERE id = %s", (family_id,))
    conn.commit()
    return redirect('/view')

# ✅ Modify route placeholder
@app.route('/modify/<int:family_id>')
def modify_family(family_id):
    return f"<h2>🛠️ Modify page for Family ID {family_id} coming soon...</h2><br><a href='/view'>← Back</a>"

@app.route('/about')
def about():
    return render_template('about.html')


# ✅ Run the server
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
