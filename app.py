from flask import Flask, request, redirect, session, send_file, abort, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import sqlite3
import os
import uuid
import secrets
import shutil
import html
import mimetypes
import datetime
import io
import base64
import pyotp
import qrcode

app = Flask(__name__)
app.secret_key = os.environ.get("MYCLOUD_SECRET", "change-this-secret-key-before-public-use")
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024 * 1024

DATABASE = "cloud.db"
STORAGE = "storage"

os.makedirs(STORAGE, exist_ok=True)


def db():
    c = sqlite3.connect(DATABASE)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    return c


def now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def esc(value):
    return html.escape(str(value))


def size_format(size):
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(size)

    for unit in units:
        if value < 1024:
            return f"{value:.1f} {unit}"
        value /= 1024

    return f"{value:.1f} PB"


def init_db():
    c = db()

    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            admin INTEGER NOT NULL DEFAULT 0,
            quota INTEGER NOT NULL,
            totp_secret TEXT,
            totp_enabled INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS folders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            parent_id INTEGER,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(parent_id) REFERENCES folders(id) ON DELETE CASCADE
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            folder_id INTEGER,
            name TEXT NOT NULL,
            stored_name TEXT NOT NULL,
            size INTEGER NOT NULL,
            mime TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(folder_id) REFERENCES folders(id) ON DELETE CASCADE
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS shares (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id INTEGER NOT NULL,
            owner_id INTEGER NOT NULL,
            target_user_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(file_id, target_user_id),
            FOREIGN KEY(file_id) REFERENCES files(id) ON DELETE CASCADE,
            FOREIGN KEY(owner_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(target_user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS public_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id INTEGER NOT NULL,
            owner_id INTEGER NOT NULL,
            token TEXT UNIQUE NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(file_id) REFERENCES files(id) ON DELETE CASCADE,
            FOREIGN KEY(owner_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS activity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            action TEXT NOT NULL,
            details TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE SET NULL
        )
    """)

    root = c.execute(
        "SELECT id FROM users WHERE username = ?",
        ("root",)
    ).fetchone()

    if not root:
        c.execute("""
            INSERT INTO users
            (username,password_hash,admin,quota,created_at)
            VALUES (?,?,?,?,?)
        """, (
            "root",
            generate_password_hash("rootadmin0"),
            1,
            100 * 1024 ** 3,
            now()
        ))

    c.commit()
    c.close()


def logged():
    return "user_id" in session


def admin():
    return logged() and session.get("admin", False)


def log(action, details=""):
    c = db()

    c.execute("""
        INSERT INTO activity
        (user_id,action,details,created_at)
        VALUES (?,?,?,?)
    """, (
        session.get("user_id"),
        action,
        details,
        now()
    ))

    c.commit()
    c.close()


def used_space(user_id):
    c = db()

    r = c.execute("""
        SELECT COALESCE(SUM(size),0) used
        FROM files
        WHERE user_id=?
    """, (user_id,)).fetchone()

    c.close()

    return r["used"]


def get_user():
    if not logged():
        return None

    c = db()

    user = c.execute(
        "SELECT * FROM users WHERE id=?",
        (session["user_id"],)
    ).fetchone()

    c.close()

    return user


def owned_file(file_id):
    c = db()

    f = c.execute("""
        SELECT *
        FROM files
        WHERE id=? AND user_id=?
    """, (
        file_id,
        session["user_id"]
    )).fetchone()

    c.close()

    return f


def accessible_file(file_id):
    c = db()

    f = c.execute("""
        SELECT files.*
        FROM files
        WHERE files.id=?
        AND (
            files.user_id=?
            OR EXISTS (
                SELECT 1 FROM shares
                WHERE shares.file_id=files.id
                AND shares.target_user_id=?
            )
        )
    """, (
        file_id,
        session["user_id"],
        session["user_id"]
    )).fetchone()

    c.close()

    return f


def file_path(f):
    return os.path.join(
        STORAGE,
        str(f["user_id"]),
        f["stored_name"]
    )


CSS = """
<style>
*{box-sizing:border-box}
:root{
--bg:#f6f7f9;
--surface:#fff;
--surface2:#fafafa;
--border:#e4e4e7;
--text:#18181b;
--muted:#71717a;
--primary:#2563eb;
--primary-hover:#1d4ed8;
--danger:#dc2626;
--shadow:0 1px 3px rgba(0,0,0,.05)
}
html.dark{
--bg:#0d0d0f;
--surface:#18181b;
--surface2:#202024;
--border:#303036;
--text:#f4f4f5;
--muted:#a1a1aa;
--primary:#3b82f6;
--primary-hover:#60a5fa;
--danger:#f87171;
--shadow:none
}
body{
margin:0;
background:var(--bg);
color:var(--text);
font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif
}
a{color:inherit}
button,input{font:inherit}
.topbar{
height:64px;
background:var(--surface);
border-bottom:1px solid var(--border);
display:flex;
align-items:center;
justify-content:space-between;
padding:0 30px;
position:sticky;
top:0;
z-index:20
}
.brand{
display:flex;
align-items:center;
gap:10px;
text-decoration:none;
font-weight:650
}
.logo{
width:32px;
height:32px;
border-radius:9px;
background:var(--text);
color:var(--surface);
display:grid;
place-items:center;
font-weight:700
}
.nav{
display:flex;
align-items:center;
gap:20px
}
.nav a,.nav button{
font-size:14px;
text-decoration:none;
color:var(--muted);
background:none;
border:0;
cursor:pointer
}
.nav a:hover,.nav button:hover{color:var(--text)}
.page{
max-width:1150px;
margin:42px auto;
padding:0 22px
}
.header{
display:flex;
align-items:center;
justify-content:space-between;
gap:20px;
margin-bottom:25px
}
.header h1{
font-size:27px;
margin:0;
letter-spacing:-.5px
}
.header p{
margin:7px 0 0;
color:var(--muted);
font-size:14px
}
.actions{
display:flex;
gap:10px;
flex-wrap:wrap
}
.btn{
border:1px solid var(--border);
background:var(--surface);
color:var(--text);
padding:9px 14px;
border-radius:7px;
cursor:pointer;
text-decoration:none;
font-size:14px
}
.btn:hover{background:var(--surface2)}
.btn.primary{
background:var(--primary);
border-color:var(--primary);
color:white
}
.btn.primary:hover{background:var(--primary-hover)}
.btn.danger{
color:var(--danger)
}
.card{
background:var(--surface);
border:1px solid var(--border);
border-radius:10px;
box-shadow:var(--shadow);
margin-bottom:18px
}
.storage{padding:18px}
.storage-top{
display:flex;
justify-content:space-between;
font-size:13px;
margin-bottom:11px
}
.muted{color:var(--muted)}
.progress{
height:6px;
background:var(--surface2);
border-radius:20px;
overflow:hidden
}
.progress-fill{
height:100%;
background:var(--primary);
border-radius:20px
}
.toolbar{
padding:13px;
display:flex;
gap:10px;
align-items:center
}
.search{
flex:1;
height:40px;
border:1px solid var(--border);
border-radius:7px;
padding:0 12px;
background:var(--surface);
color:var(--text);
outline:none
}
.search:focus{border-color:var(--primary)}
.drop{
border:2px dashed var(--border);
border-radius:10px;
padding:24px;
text-align:center;
color:var(--muted);
margin-bottom:18px;
transition:.15s
}
.drop.drag{
border-color:var(--primary);
background:rgba(37,99,235,.05)
}
.upload-progress{
display:none;
margin-top:15px
}
.upload-text{
font-size:13px;
margin-bottom:7px
}
.list-header,.row{
display:grid;
grid-template-columns:minmax(0,1fr) 110px 170px 310px;
align-items:center;
gap:10px
}
.list-header{
padding:12px 18px;
font-size:12px;
color:var(--muted);
border-bottom:1px solid var(--border)
}
.row{
padding:13px 18px;
border-bottom:1px solid var(--border);
min-height:66px
}
.row:last-child{border-bottom:0}
.item{
display:flex;
align-items:center;
gap:12px;
min-width:0
}
.icon{
width:36px;
height:36px;
border-radius:8px;
background:var(--surface2);
display:grid;
place-items:center;
flex-shrink:0;
font-size:17px
}
.item-name{
overflow:hidden;
text-overflow:ellipsis;
white-space:nowrap;
font-size:14px;
font-weight:500
}
.small{
font-size:12px;
color:var(--muted);
margin-top:3px
}
.row-actions{
display:flex;
justify-content:flex-end;
align-items:center;
gap:12px;
flex-wrap:wrap
}
.row-actions a,.link-button{
font-size:12px;
color:var(--muted);
background:none;
border:0;
padding:0;
cursor:pointer;
text-decoration:none
}
.row-actions a:hover,.link-button:hover{color:var(--primary)}
.link-button.delete{color:var(--danger)}
form.inline{display:inline;margin:0}
.empty{
padding:65px 20px;
text-align:center;
color:var(--muted)
}
.login-wrap{
min-height:100vh;
display:grid;
place-items:center;
padding:20px
}
.login{
width:390px;
max-width:100%;
padding:34px;
background:var(--surface);
border:1px solid var(--border);
border-radius:12px
}
.login h1{margin:32px 0 5px;font-size:24px}
.login p{margin:0 0 25px;color:var(--muted);font-size:14px}
.field{
display:flex;
flex-direction:column;
gap:7px;
margin-bottom:16px;
font-size:13px;
font-weight:500
}
.field input,.field select{
height:41px;
border:1px solid var(--border);
border-radius:7px;
padding:0 11px;
background:var(--surface);
color:var(--text);
outline:none
}
.field input:focus{border-color:var(--primary)}
.full{width:100%}
.alert{
padding:11px 12px;
border:1px solid #fecaca;
background:#fff1f2;
color:#991b1b;
border-radius:7px;
font-size:13px;
margin-bottom:18px
}
html.dark .alert{
background:#32151a;
border-color:#64222d;
color:#fda4af
}
.grid{
display:grid;
grid-template-columns:repeat(2,minmax(0,1fr));
gap:18px
}
.panel{padding:22px}
.panel h2{
margin:0 0 18px;
font-size:17px
}
.admin-grid{
display:grid;
grid-template-columns:1fr 1fr 140px auto;
gap:12px;
align-items:end
}
.user-row{
display:grid;
grid-template-columns:1fr 140px 170px 210px;
gap:15px;
align-items:center;
padding:15px 18px;
border-bottom:1px solid var(--border);
font-size:13px
}
.user-row:last-child{border-bottom:0}
.badge{
display:inline-block;
padding:3px 7px;
border-radius:20px;
background:var(--surface2);
font-size:11px;
color:var(--muted)
}
.activity{
padding:14px 18px;
border-bottom:1px solid var(--border)
}
.activity:last-child{border-bottom:0}
.activity-title{font-size:13px;font-weight:500}
.activity-details{font-size:12px;color:var(--muted);margin-top:4px}
.preview{
max-width:100%;
max-height:70vh;
display:block;
margin:auto;
border-radius:8px
}
.modal{
display:none;
position:fixed;
inset:0;
background:rgba(0,0,0,.55);
z-index:100;
align-items:center;
justify-content:center;
padding:20px
}
.modal.show{display:flex}
.modal-box{
width:440px;
max-width:100%;
background:var(--surface);
border:1px solid var(--border);
border-radius:12px;
padding:22px
}
.modal-box h2{margin:0 0 18px}
.share-url{
word-break:break-all;
background:var(--surface2);
padding:10px;
border-radius:7px;
font-size:12px
}
.breadcrumb{
display:flex;
gap:7px;
align-items:center;
font-size:13px;
margin-bottom:17px;
color:var(--muted);
flex-wrap:wrap
}
.breadcrumb a{text-decoration:none}
.breadcrumb a:hover{color:var(--primary)}
.qr{
display:block;
width:210px;
height:210px;
margin:15px auto;
background:white;
padding:8px;
border-radius:8px
}
@media(max-width:850px){
.topbar{padding:0 15px}
.nav .username{display:none}
.page{margin:25px auto;padding:0 13px}
.header{align-items:flex-start;flex-direction:column}
.list-header{display:none}
.row{
grid-template-columns:1fr;
gap:9px
}
.row-actions{justify-content:flex-start;padding-left:48px}
.row>.muted{padding-left:48px}
.grid{grid-template-columns:1fr}
.admin-grid{grid-template-columns:1fr}
.user-row{grid-template-columns:1fr}
.toolbar{flex-direction:column;align-items:stretch}
}
</style>
"""


JS = """
<script>
(function(){
    if(localStorage.getItem("theme")==="dark"){
        document.documentElement.classList.add("dark");
    }
})();

function toggleTheme(){
    document.documentElement.classList.toggle("dark");
    localStorage.setItem(
        "theme",
        document.documentElement.classList.contains("dark") ? "dark" : "light"
    );
}

function modal(id){
    document.getElementById(id).classList.add("show");
}

function closeModal(id){
    document.getElementById(id).classList.remove("show");
}

function copyText(id){
    const e=document.getElementById(id);
    navigator.clipboard.writeText(e.innerText);
}

function uploadFiles(files){
    if(!files || files.length===0) return;

    const folder=document.body.dataset.folder || "";
    const data=new FormData();

    for(const f of files){
        data.append("files",f);
    }

    data.append("folder_id",folder);

    const xhr=new XMLHttpRequest();
    xhr.open("POST","/upload");

    const box=document.getElementById("uploadProgress");
    const bar=document.getElementById("uploadBar");
    const text=document.getElementById("uploadText");

    if(box) box.style.display="block";

    xhr.upload.onprogress=function(e){
        if(e.lengthComputable){
            const p=Math.round((e.loaded/e.total)*100);
            if(bar) bar.style.width=p+"%";
            if(text) text.innerText="Uploading... "+p+"%";
        }
    };

    xhr.onload=function(){
        if(xhr.status>=200 && xhr.status<300){
            if(text) text.innerText="Upload complete";
            location.reload();
        }else{
            if(text) text.innerText=xhr.responseText || "Upload failed";
        }
    };

    xhr.onerror=function(){
        if(text) text.innerText="Upload failed";
    };

    xhr.send(data);
}

document.addEventListener("DOMContentLoaded",function(){
    const drop=document.getElementById("dropZone");

    if(drop){
        drop.addEventListener("dragover",function(e){
            e.preventDefault();
            drop.classList.add("drag");
        });

        drop.addEventListener("dragleave",function(){
            drop.classList.remove("drag");
        });

        drop.addEventListener("drop",function(e){
            e.preventDefault();
            drop.classList.remove("drag");
            uploadFiles(e.dataTransfer.files);
        });
    }
});
</script>
"""


def layout(content, title="MyCloud", navbar=True, folder_id=""):
    nav = ""

    if navbar and logged():
        admin_link = ""

        if session.get("admin"):
            admin_link = '<a href="/admin">Administration</a>'

        nav = f"""
        <header class="topbar">
            <a href="/" class="brand">
                <div class="logo">M</div>
                MyCloud
            </a>

            <nav class="nav">
                <a href="/">Files</a>
                <a href="/shared">Shared</a>
                <a href="/activity">Activity</a>
                <a href="/settings">Settings</a>
                {admin_link}
                <span class="username">{esc(session.get("username",""))}</span>
                <button onclick="toggleTheme()">Theme</button>
                <a href="/logout">Sign out</a>
            </nav>
        </header>
        """

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title>
{CSS}
<script>
if(localStorage.getItem("theme")==="dark"){{
document.documentElement.classList.add("dark");
}}
</script>
</head>
<body data-folder="{folder_id}">
{nav}
{content}
{JS}
</body>
</html>"""


def folder_valid(folder_id):
    if not folder_id:
        return None

    c = db()

    folder = c.execute("""
        SELECT * FROM folders
        WHERE id=? AND user_id=?
    """, (
        folder_id,
        session["user_id"]
    )).fetchone()

    c.close()

    return folder


def breadcrumbs(folder):
    crumbs = []

    c = db()

    current = folder

    while current:
        crumbs.append(current)

        if current["parent_id"]:
            current = c.execute("""
                SELECT * FROM folders
                WHERE id=? AND user_id=?
            """, (
                current["parent_id"],
                session["user_id"]
            )).fetchone()
        else:
            current = None

    c.close()

    crumbs.reverse()

    result = '<a href="/">My files</a>'

    for crumb in crumbs:
        result += f' <span>/</span> <a href="/?folder={crumb["id"]}">{esc(crumb["name"])}</a>'

    return result


@app.route("/login", methods=["GET", "POST"])
def login():
    if logged():
        return redirect("/")

    error = ""

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        c = db()

        user = c.execute(
            "SELECT * FROM users WHERE username=?",
            (username,)
        ).fetchone()

        c.close()

        if user and check_password_hash(user["password_hash"], password):
            if user["totp_enabled"]:
                session.clear()
                session["pending_2fa"] = user["id"]
                return redirect("/2fa")

            session.clear()
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["admin"] = bool(user["admin"])

            log("Login", "Signed in")

            return redirect("/")

        error = '<div class="alert">Incorrect username or password.</div>'

    content = f"""
    <main class="login-wrap">
        <section class="login">
            <div class="brand">
                <div class="logo">M</div>
                MyCloud
            </div>

            <h1>Welcome back</h1>
            <p>Sign in to access your private cloud.</p>

            {error}

            <form method="POST">
                <label class="field">
                    Username
                    <input name="username" autocomplete="username" required autofocus>
                </label>

                <label class="field">
                    Password
                    <input type="password" name="password" autocomplete="current-password" required>
                </label>

                <button class="btn primary full">Sign in</button>
            </form>
        </section>
    </main>
    """

    return layout(content, "Sign in · MyCloud", False)


@app.route("/2fa", methods=["GET", "POST"])
def two_factor_login():
    user_id = session.get("pending_2fa")

    if not user_id:
        return redirect("/login")

    c = db()
    user = c.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    c.close()

    if not user:
        session.clear()
        return redirect("/login")

    error = ""

    if request.method == "POST":
        code = request.form.get("code", "").replace(" ", "")

        if pyotp.TOTP(user["totp_secret"]).verify(code, valid_window=1):
            session.clear()
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["admin"] = bool(user["admin"])

            log("Login", "Signed in using two-factor authentication")

            return redirect("/")

        error = '<div class="alert">Invalid authentication code.</div>'

    content = f"""
    <main class="login-wrap">
        <section class="login">
            <div class="brand">
                <div class="logo">M</div>
                MyCloud
            </div>

            <h1>Two-factor authentication</h1>
            <p>Enter the code from your authenticator app.</p>

            {error}

            <form method="POST">
                <label class="field">
                    Authentication code
                    <input name="code" inputmode="numeric" autocomplete="one-time-code" required autofocus>
                </label>

                <button class="btn primary full">Verify</button>
            </form>
        </section>
    </main>
    """

    return layout(content, "Two-factor authentication", False)


@app.route("/")
def home():
    if not logged():
        return redirect("/login")

    folder_id = request.args.get("folder", type=int)
    search = request.args.get("q", "").strip()

    folder = None

    if folder_id:
        folder = folder_valid(folder_id)

        if not folder:
            abort(404)

    c = db()

    user = c.execute(
        "SELECT * FROM users WHERE id=?",
        (session["user_id"],)
    ).fetchone()

    if search:
        like = f"%{search}%"

        folders = c.execute("""
            SELECT * FROM folders
            WHERE user_id=? AND name LIKE ?
            ORDER BY name
        """, (
            session["user_id"],
            like
        )).fetchall()

        files = c.execute("""
            SELECT * FROM files
            WHERE user_id=? AND name LIKE ?
            ORDER BY created_at DESC
        """, (
            session["user_id"],
            like
        )).fetchall()

    else:
        if folder_id:
            folders = c.execute("""
                SELECT * FROM folders
                WHERE user_id=? AND parent_id=?
                ORDER BY name
            """, (
                session["user_id"],
                folder_id
            )).fetchall()

            files = c.execute("""
                SELECT * FROM files
                WHERE user_id=? AND folder_id=?
                ORDER BY created_at DESC
            """, (
                session["user_id"],
                folder_id
            )).fetchall()

        else:
            folders = c.execute("""
                SELECT * FROM folders
                WHERE user_id=? AND parent_id IS NULL
                ORDER BY name
            """, (
                session["user_id"],
            )).fetchall()

            files = c.execute("""
                SELECT * FROM files
                WHERE user_id=? AND folder_id IS NULL
                ORDER BY created_at DESC
            """, (
                session["user_id"],
            )).fetchall()

    c.close()

    used = used_space(session["user_id"])
    quota = user["quota"]

    percentage = min((used / quota * 100) if quota else 0, 100)

    rows = ""

    for folder_item in folders:
        rows += f"""
        <div class="row">
            <div class="item">
                <div class="icon">📁</div>
                <div>
                    <a class="item-name" href="/?folder={folder_item['id']}">
                        {esc(folder_item["name"])}
                    </a>
                    <div class="small">Folder</div>
                </div>
            </div>

            <span class="muted">—</span>
            <span class="muted">{esc(folder_item["created_at"])}</span>

            <div class="row-actions">
                <form class="inline" method="POST" action="/folder/delete/{folder_item['id']}"
                onsubmit="return confirm('Delete this folder and everything inside it?')">
                    <button class="link-button delete">Delete</button>
                </form>
            </div>
        </div>
        """

    for f in files:
        icon = "🖼️" if (f["mime"] or "").startswith("image/") else "📄"

        preview = ""

        if (f["mime"] or "").startswith("image/"):
            preview = f'<a href="/preview/{f["id"]}" target="_blank">Preview</a>'

        rows += f"""
        <div class="row">
            <div class="item">
                <div class="icon">{icon}</div>
                <div style="min-width:0">
                    <div class="item-name">{esc(f["name"])}</div>
                    <div class="small">{esc(f["mime"] or "Unknown type")}</div>
                </div>
            </div>

            <span class="muted">{size_format(f["size"])}</span>
            <span class="muted">{esc(f["created_at"])}</span>

            <div class="row-actions">
                {preview}
                <a href="/download/{f['id']}">Download</a>
                <a href="/file/{f['id']}">Details</a>
                <a href="/share/{f['id']}">Share</a>

                <form class="inline" method="POST" action="/delete/{f['id']}"
                onsubmit="return confirm('Delete this file?')">
                    <button class="link-button delete">Delete</button>
                </form>
            </div>
        </div>
        """

    if not rows:
        rows = """
        <div class="empty">
            Nothing here yet.
        </div>
        """

    breadcrumb = breadcrumbs(folder) if folder else '<a href="/">My files</a>'

    parent_value = folder_id or ""

    content = f"""
    <main class="page">

        <div class="header">
            <div>
                <h1>Files</h1>
                <p>Your private cloud storage.</p>
            </div>

            <div class="actions">
                <button class="btn" onclick="modal('folderModal')">New folder</button>

                <label class="btn primary">
                    Upload
                    <input type="file" multiple hidden onchange="uploadFiles(this.files)">
                </label>
            </div>
        </div>

        <div class="breadcrumb">
            {breadcrumb}
        </div>

        <section class="card storage">
            <div class="storage-top">
                <span>Storage</span>
                <span class="muted">{size_format(used)} of {size_format(quota)}</span>
            </div>

            <div class="progress">
                <div class="progress-fill" style="width:{percentage}%"></div>
            </div>
        </section>

        <div id="dropZone" class="drop">
            Drop files here to upload

            <div id="uploadProgress" class="upload-progress">
                <div id="uploadText" class="upload-text">Uploading...</div>

                <div class="progress">
                    <div id="uploadBar" class="progress-fill" style="width:0"></div>
                </div>
            </div>
        </div>

        <section class="card">
            <form class="toolbar" method="GET">
                <input class="search" name="q" value="{esc(search)}" placeholder="Search files and folders...">

                <button class="btn">Search</button>

                <a class="btn" href="/">Clear</a>
            </form>

            <div class="list-header">
                <span>Name</span>
                <span>Size</span>
                <span>Created</span>
                <span></span>
            </div>

            {rows}
        </section>

    </main>

    <div class="modal" id="folderModal" onclick="if(event.target===this)closeModal('folderModal')">
        <div class="modal-box">
            <h2>Create folder</h2>

            <form method="POST" action="/folder/create">
                <input type="hidden" name="parent_id" value="{parent_value}">

                <label class="field">
                    Folder name
                    <input name="name" maxlength="100" required autofocus>
                </label>

                <div class="actions">
                    <button class="btn primary">Create folder</button>
                    <button type="button" class="btn" onclick="closeModal('folderModal')">Cancel</button>
                </div>
            </form>
        </div>
    </div>
    """

    return layout(
        content,
        "Files · MyCloud",
        True,
        parent_value
    )


@app.route("/folder/create", methods=["POST"])
def create_folder():
    if not logged():
        abort(403)

    name = request.form.get("name", "").strip()
    parent_id = request.form.get("parent_id", type=int)

    if not name or len(name) > 100:
        abort(400)

    if parent_id and not folder_valid(parent_id):
        abort(404)

    c = db()

    c.execute("""
        INSERT INTO folders
        (user_id,parent_id,name,created_at)
        VALUES (?,?,?,?)
    """, (
        session["user_id"],
        parent_id,
        name,
        now()
    ))

    c.commit()
    c.close()

    log("Folder created", name)

    if parent_id:
        return redirect(f"/?folder={parent_id}")

    return redirect("/")


@app.route("/folder/delete/<int:folder_id>", methods=["POST"])
def delete_folder(folder_id):
    if not logged():
        abort(403)

    folder = folder_valid(folder_id)

    if not folder:
        abort(404)

    c = db()

    file_rows = c.execute("""
        WITH RECURSIVE tree(id) AS (
            SELECT id FROM folders WHERE id=? AND user_id=?
            UNION ALL
            SELECT folders.id
            FROM folders
            JOIN tree ON folders.parent_id=tree.id
            WHERE folders.user_id=?
        )
        SELECT files.*
        FROM files
        WHERE files.folder_id IN (SELECT id FROM tree)
        AND files.user_id=?
    """, (
        folder_id,
        session["user_id"],
        session["user_id"],
        session["user_id"]
    )).fetchall()

    for f in file_rows:
        path = file_path(f)

        if os.path.isfile(path):
            os.remove(path)

    c.execute("""
        DELETE FROM folders
        WHERE id=? AND user_id=?
    """, (
        folder_id,
        session["user_id"]
    ))

    c.commit()
    c.close()

    log("Folder deleted", folder["name"])

    return redirect("/")


@app.route("/upload", methods=["POST"])
def upload():
    if not logged():
        return "Not authenticated", 403

    files = request.files.getlist("files")
    folder_id = request.form.get("folder_id", type=int)

    if folder_id and not folder_valid(folder_id):
        return "Invalid folder", 400

    if not files:
        return "No files", 400

    user = get_user()

    current = used_space(session["user_id"])

    total = 0

    for f in files:
        f.seek(0, os.SEEK_END)
        total += f.tell()
        f.seek(0)

    if current + total > user["quota"]:
        return "Storage quota exceeded", 413

    directory = os.path.join(
        STORAGE,
        str(session["user_id"])
    )

    os.makedirs(directory, exist_ok=True)

    c = db()

    uploaded_names = []

    for f in files:
        if not f.filename:
            continue

        name = secure_filename(f.filename)

        if not name:
            continue

        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(0)

        stored_name = str(uuid.uuid4())

        path = os.path.join(
            directory,
            stored_name
        )

        f.save(path)

        mime = f.mimetype or mimetypes.guess_type(name)[0] or "application/octet-stream"

        c.execute("""
            INSERT INTO files
            (user_id,folder_id,name,stored_name,size,mime,created_at)
            VALUES (?,?,?,?,?,?,?)
        """, (
            session["user_id"],
            folder_id,
            name,
            stored_name,
            size,
            mime,
            now()
        ))

        uploaded_names.append(name)

    c.commit()
    c.close()

    for name in uploaded_names:
        log("File uploaded", name)

    return "OK"


@app.route("/download/<int:file_id>")
def download(file_id):
    if not logged():
        return redirect("/login")

    f = accessible_file(file_id)

    if not f:
        abort(404)

    path = file_path(f)

    if not os.path.isfile(path):
        abort(404)

    log("File downloaded", f["name"])

    return send_file(
        path,
        as_attachment=True,
        download_name=f["name"]
    )


@app.route("/preview/<int:file_id>")
def preview(file_id):
    if not logged():
        return redirect("/login")

    f = accessible_file(file_id)

    if not f:
        abort(404)

    if not (f["mime"] or "").startswith("image/"):
        abort(400)

    path = file_path(f)

    if not os.path.isfile(path):
        abort(404)

    return send_file(
        path,
        mimetype=f["mime"],
        as_attachment=False
    )


@app.route("/delete/<int:file_id>", methods=["POST"])
def delete_file(file_id):
    if not logged():
        abort(403)

    f = owned_file(file_id)

    if not f:
        abort(404)

    path = file_path(f)

    if os.path.isfile(path):
        os.remove(path)

    c = db()

    c.execute("""
        DELETE FROM files
        WHERE id=? AND user_id=?
    """, (
        file_id,
        session["user_id"]
    ))

    c.commit()
    c.close()

    log("File deleted", f["name"])

    return redirect("/")


@app.route("/file/<int:file_id>")
def file_details(file_id):
    if not logged():
        return redirect("/login")

    f = accessible_file(file_id)

    if not f:
        abort(404)

    owner = f["user_id"] == session["user_id"]

    actions = f"""
    <a class="btn primary" href="/download/{f['id']}">Download</a>
    """

    if owner:
        actions += f"""
        <a class="btn" href="/share/{f['id']}">Share</a>
        """

    preview_html = ""

    if (f["mime"] or "").startswith("image/"):
        preview_html = f"""
        <div style="margin-top:25px">
            <img class="preview" src="/preview/{f['id']}">
        </div>
        """

    content = f"""
    <main class="page">
        <div class="header">
            <div>
                <h1>{esc(f["name"])}</h1>
                <p>File information</p>
            </div>

            <div class="actions">
                {actions}
            </div>
        </div>

        <section class="card panel">
            <h2>Metadata</h2>

            <p><b>Name:</b> {esc(f["name"])}</p>
            <p><b>Size:</b> {size_format(f["size"])}</p>
            <p><b>Type:</b> {esc(f["mime"] or "Unknown")}</p>
            <p><b>Uploaded:</b> {esc(f["created_at"])}</p>

            {preview_html}
        </section>
    </main>
    """

    return layout(content, f"{f['name']} · MyCloud")


@app.route("/share/<int:file_id>", methods=["GET", "POST"])
def share_file(file_id):
    if not logged():
        return redirect("/login")

    f = owned_file(file_id)

    if not f:
        abort(404)

    message = ""

    if request.method == "POST":
        action = request.form.get("action")

        if action == "user":
            username = request.form.get("username", "").strip()

            c = db()

            target = c.execute(
                "SELECT * FROM users WHERE username=?",
                (username,)
            ).fetchone()

            if not target:
                message = '<div class="alert">User not found.</div>'

            elif target["id"] == session["user_id"]:
                message = '<div class="alert">You already own this file.</div>'

            else:
                c.execute("""
                    INSERT OR IGNORE INTO shares
                    (file_id,owner_id,target_user_id,created_at)
                    VALUES (?,?,?,?)
                """, (
                    file_id,
                    session["user_id"],
                    target["id"],
                    now()
                ))

                c.commit()

                message = f"""
                <div class="card panel">
                    File shared with {esc(username)}.
                </div>
                """

                log("File shared", f'{f["name"]} with {username}')

            c.close()

        elif action == "public":
            c = db()

            existing = c.execute("""
                SELECT * FROM public_links
                WHERE file_id=? AND owner_id=?
            """, (
                file_id,
                session["user_id"]
            )).fetchone()

            if not existing:
                token = secrets.token_urlsafe(32)

                c.execute("""
                    INSERT INTO public_links
                    (file_id,owner_id,token,created_at)
                    VALUES (?,?,?,?)
                """, (
                    file_id,
                    session["user_id"],
                    token,
                    now()
                ))

                c.commit()

                log("Public link created", f["name"])

            c.close()

        elif action == "revoke":
            c = db()

            c.execute("""
                DELETE FROM public_links
                WHERE file_id=? AND owner_id=?
            """, (
                file_id,
                session["user_id"]
            ))

            c.commit()
            c.close()

            log("Public link revoked", f["name"])

    c = db()

    public = c.execute("""
        SELECT * FROM public_links
        WHERE file_id=? AND owner_id=?
    """, (
        file_id,
        session["user_id"]
    )).fetchone()

    shared_users = c.execute("""
        SELECT users.username, shares.id
        FROM shares
        JOIN users ON users.id=shares.target_user_id
        WHERE shares.file_id=? AND shares.owner_id=?
        ORDER BY users.username
    """, (
        file_id,
        session["user_id"]
    )).fetchall()

    c.close()

    public_html = """
    <form method="POST">
        <input type="hidden" name="action" value="public">
        <button class="btn primary">Create public link</button>
    </form>
    """

    if public:
        url = request.host_url.rstrip("/") + "/s/" + public["token"]

        public_html = f"""
        <div id="publicUrl" class="share-url">{esc(url)}</div>

        <div class="actions" style="margin-top:12px">
            <button class="btn" onclick="copyText('publicUrl')">Copy link</button>

            <form method="POST">
                <input type="hidden" name="action" value="revoke">
                <button class="btn danger">Revoke link</button>
            </form>
        </div>
        """

    shared_html = ""

    for u in shared_users:
        shared_html += f"""
        <div class="activity">
            <div class="activity-title">{esc(u["username"])}</div>

            <form method="POST" action="/share/remove/{u['id']}">
                <button class="link-button delete">Remove access</button>
            </form>
        </div>
        """

    if not shared_html:
        shared_html = '<div class="empty">Not shared with any users.</div>'

    content = f"""
    <main class="page">
        <div class="header">
            <div>
                <h1>Share file</h1>
                <p>{esc(f["name"])}</p>
            </div>
        </div>

        {message}

        <div class="grid">

            <section class="card panel">
                <h2>Share with a user</h2>

                <form method="POST">
                    <input type="hidden" name="action" value="user">

                    <label class="field">
                        Username
                        <input name="username" required>
                    </label>

                    <button class="btn primary">Share</button>
                </form>
            </section>

            <section class="card panel">
                <h2>Public link</h2>
                {public_html}
            </section>

        </div>

        <section class="card">
            <div class="panel">
                <h2>People with access</h2>
            </div>

            {shared_html}
        </section>
    </main>
    """

    return layout(content, "Share · MyCloud")


@app.route("/share/remove/<int:share_id>", methods=["POST"])
def remove_share(share_id):
    if not logged():
        abort(403)

    c = db()

    share = c.execute("""
        SELECT shares.*, files.name
        FROM shares
        JOIN files ON files.id=shares.file_id
        WHERE shares.id=? AND shares.owner_id=?
    """, (
        share_id,
        session["user_id"]
    )).fetchone()

    if not share:
        c.close()
        abort(404)

    c.execute("""
        DELETE FROM shares
        WHERE id=? AND owner_id=?
    """, (
        share_id,
        session["user_id"]
    ))

    c.commit()
    c.close()

    log("Share removed", share["name"])

    return redirect(f"/share/{share['file_id']}")


@app.route("/shared")
def shared():
    if not logged():
        return redirect("/login")

    c = db()

    files = c.execute("""
        SELECT
            files.*,
            users.username owner_username
        FROM shares
        JOIN files ON files.id=shares.file_id
        JOIN users ON users.id=files.user_id
        WHERE shares.target_user_id=?
        ORDER BY shares.created_at DESC
    """, (
        session["user_id"],
    )).fetchall()

    c.close()

    rows = ""

    for f in files:
        preview = ""

        if (f["mime"] or "").startswith("image/"):
            preview = f'<a href="/preview/{f["id"]}" target="_blank">Preview</a>'

        rows += f"""
        <div class="row">
            <div class="item">
                <div class="icon">📄</div>
                <div>
                    <div class="item-name">{esc(f["name"])}</div>
                    <div class="small">Shared by {esc(f["owner_username"])}</div>
                </div>
            </div>

            <span class="muted">{size_format(f["size"])}</span>
            <span class="muted">{esc(f["created_at"])}</span>

            <div class="row-actions">
                {preview}
                <a href="/file/{f['id']}">Details</a>
                <a href="/download/{f['id']}">Download</a>
            </div>
        </div>
        """

    if not rows:
        rows = '<div class="empty">No files have been shared with you.</div>'

    content = f"""
    <main class="page">
        <div class="header">
            <div>
                <h1>Shared with me</h1>
                <p>Files other users have shared with you.</p>
            </div>
        </div>

        <section class="card">
            <div class="list-header">
                <span>Name</span>
                <span>Size</span>
                <span>Created</span>
                <span></span>
            </div>

            {rows}
        </section>
    </main>
    """

    return layout(content, "Shared · MyCloud")


@app.route("/s/<token>")
def public_file(token):
    c = db()

    f = c.execute("""
        SELECT files.*
        FROM public_links
        JOIN files ON files.id=public_links.file_id
        WHERE public_links.token=?
    """, (
        token,
    )).fetchone()

    c.close()

    if not f:
        abort(404)

    path = file_path(f)

    if not os.path.isfile(path):
        abort(404)

    preview = ""

    if (f["mime"] or "").startswith("image/"):
        preview = f"""
        <img class="preview" src="/s/{token}/raw">
        """

    content = f"""
    <main class="login-wrap">
        <section class="login" style="width:600px">
            <div class="brand">
                <div class="logo">M</div>
                MyCloud
            </div>

            <h1>{esc(f["name"])}</h1>

            <p>
                {size_format(f["size"])} · {esc(f["mime"] or "Unknown type")}
            </p>

            {preview}

            <div style="margin-top:20px">
                <a class="btn primary" href="/s/{token}/download">
                    Download
                </a>
            </div>
        </section>
    </main>
    """

    return layout(content, f["name"], False)


@app.route("/s/<token>/raw")
def public_raw(token):
    c = db()

    f = c.execute("""
        SELECT files.*
        FROM public_links
        JOIN files ON files.id=public_links.file_id
        WHERE public_links.token=?
    """, (token,)).fetchone()

    c.close()

    if not f:
        abort(404)

    if not (f["mime"] or "").startswith("image/"):
        abort(400)

    return send_file(
        file_path(f),
        mimetype=f["mime"]
    )


@app.route("/s/<token>/download")
def public_download(token):
    c = db()

    f = c.execute("""
        SELECT files.*
        FROM public_links
        JOIN files ON files.id=public_links.file_id
        WHERE public_links.token=?
    """, (token,)).fetchone()

    c.close()

    if not f:
        abort(404)

    return send_file(
        file_path(f),
        as_attachment=True,
        download_name=f["name"]
    )


@app.route("/settings")
def settings():
    if not logged():
        return redirect("/login")

    user = get_user()

    status = "Enabled" if user["totp_enabled"] else "Disabled"

    two_factor_button = """
    <a class="btn primary" href="/settings/2fa">Set up two-factor authentication</a>
    """

    if user["totp_enabled"]:
        two_factor_button = """
        <form method="POST" action="/settings/2fa/disable"
        onsubmit="return confirm('Disable two-factor authentication?')">
            <button class="btn danger">Disable two-factor authentication</button>
        </form>
        """

    content = f"""
    <main class="page">
        <div class="header">
            <div>
                <h1>Settings</h1>
                <p>Manage your account and security.</p>
            </div>
        </div>

        <div class="grid">

            <section class="card panel">
                <h2>Change password</h2>

                <form method="POST" action="/settings/password">
                    <label class="field">
                        Current password
                        <input type="password" name="current" required>
                    </label>

                    <label class="field">
                        New password
                        <input type="password" name="new" minlength="8" required>
                    </label>

                    <button class="btn primary">Change password</button>
                </form>
            </section>

            <section class="card panel">
                <h2>Two-factor authentication</h2>

                <p class="muted">
                    Status: {status}
                </p>

                {two_factor_button}
            </section>

        </div>
    </main>
    """

    return layout(content, "Settings · MyCloud")


@app.route("/settings/password", methods=["POST"])
def change_password():
    if not logged():
        abort(403)

    current = request.form.get("current", "")
    new = request.form.get("new", "")

    user = get_user()

    if not check_password_hash(user["password_hash"], current):
        return "Current password is incorrect.", 400

    if len(new) < 8:
        return "Password must be at least 8 characters.", 400

    c = db()

    c.execute("""
        UPDATE users
        SET password_hash=?
        WHERE id=?
    """, (
        generate_password_hash(new),
        session["user_id"]
    ))

    c.commit()
    c.close()

    log("Password changed", "")

    return redirect("/settings")


@app.route("/settings/2fa")
def setup_2fa():
    if not logged():
        return redirect("/login")

    user = get_user()

    if user["totp_enabled"]:
        return redirect("/settings")

    secret = user["totp_secret"]

    if not secret:
        secret = pyotp.random_base32()

        c = db()

        c.execute("""
            UPDATE users
            SET totp_secret=?
            WHERE id=?
        """, (
            secret,
            session["user_id"]
        ))

        c.commit()
        c.close()

    uri = pyotp.TOTP(secret).provisioning_uri(
        name=user["username"],
        issuer_name="MyCloud"
    )

    qr = qrcode.make(uri)

    buffer = io.BytesIO()
    qr.save(buffer, format="PNG")

    qr_data = base64.b64encode(
        buffer.getvalue()
    ).decode()

    content = f"""
    <main class="page">
        <div class="header">
            <div>
                <h1>Set up 2FA</h1>
                <p>Scan the QR code with an authenticator app.</p>
            </div>
        </div>

        <section class="card panel" style="max-width:500px">
            <img class="qr" src="data:image/png;base64,{qr_data}">

            <p class="muted">
                Manual key:
            </p>

            <div class="share-url">{esc(secret)}</div>

            <form method="POST" action="/settings/2fa/enable" style="margin-top:20px">
                <label class="field">
                    Authentication code
                    <input name="code" inputmode="numeric" required>
                </label>

                <button class="btn primary">Enable 2FA</button>
            </form>
        </section>
    </main>
    """

    return layout(content, "Set up 2FA · MyCloud")


@app.route("/settings/2fa/enable", methods=["POST"])
def enable_2fa():
    if not logged():
        abort(403)

    user = get_user()

    if not user["totp_secret"]:
        abort(400)

    code = request.form.get("code", "")

    if not pyotp.TOTP(user["totp_secret"]).verify(code, valid_window=1):
        return "Invalid authentication code.", 400

    c = db()

    c.execute("""
        UPDATE users
        SET totp_enabled=1
        WHERE id=?
    """, (
        session["user_id"],
    ))

    c.commit()
    c.close()

    log("Two-factor authentication enabled", "")

    return redirect("/settings")


@app.route("/settings/2fa/disable", methods=["POST"])
def disable_2fa():
    if not logged():
        abort(403)

    c = db()

    c.execute("""
        UPDATE users
        SET totp_enabled=0,
        totp_secret=NULL
        WHERE id=?
    """, (
        session["user_id"],
    ))

    c.commit()
    c.close()

    log("Two-factor authentication disabled", "")

    return redirect("/settings")


@app.route("/activity")
def activity():
    if not logged():
        return redirect("/login")

    c = db()

    entries = c.execute("""
        SELECT *
        FROM activity
        WHERE user_id=?
        ORDER BY id DESC
        LIMIT 100
    """, (
        session["user_id"],
    )).fetchall()

    c.close()

    rows = ""

    for entry in entries:
        rows += f"""
        <div class="activity">
            <div class="activity-title">
                {esc(entry["action"])}
            </div>

            <div class="activity-details">
                {esc(entry["details"] or "")}
                ·
                {esc(entry["created_at"])}
            </div>
        </div>
        """

    if not rows:
        rows = '<div class="empty">No activity yet.</div>'

    content = f"""
    <main class="page">
        <div class="header">
            <div>
                <h1>Activity</h1>
                <p>Your recent MyCloud activity.</p>
            </div>
        </div>

        <section class="card">
            {rows}
        </section>
    </main>
    """

    return layout(content, "Activity · MyCloud")


@app.route("/admin")
def admin_panel():
    if not admin():
        abort(403)

    c = db()

    users = c.execute("""
        SELECT *
        FROM users
        ORDER BY id
    """).fetchall()

    c.close()

    rows = ""

    for user in users:
        role = "Administrator" if user["admin"] else "User"

        admin_action = ""

        if user["id"] != session["user_id"]:
            if user["admin"]:
                admin_action = f"""
                <form class="inline" method="POST" action="/admin/admin/{user['id']}">
                    <input type="hidden" name="value" value="0">
                    <button class="link-button">Remove admin</button>
                </form>
                """
            else:
                admin_action = f"""
                <form class="inline" method="POST" action="/admin/admin/{user['id']}">
                    <input type="hidden" name="value" value="1">
                    <button class="link-button">Make admin</button>
                </form>
                """

        delete = ""

        if user["id"] != session["user_id"]:
            delete = f"""
            <form class="inline" method="POST" action="/admin/delete/{user['id']}"
            onsubmit="return confirm('Delete this user and all their files?')">
                <button class="link-button delete">Delete</button>
            </form>
            """

        quota_gb = round(
            user["quota"] / 1024 ** 3,
            2
        )

        rows += f"""
        <div class="user-row">
            <div>
                <b>{esc(user["username"])}</b>
                <div class="small">
                    <span class="badge">{role}</span>
                </div>
            </div>

            <span>{size_format(used_space(user["id"]))}</span>

            <form class="inline" method="POST" action="/admin/quota/{user['id']}">
                <input
                    style="width:80px;height:34px"
                    type="number"
                    name="quota"
                    value="{quota_gb}"
                    min="0.1"
                    step="0.1"
                    required
                >
                GB
                <button class="btn">Save</button>
            </form>

            <div class="row-actions">
                {admin_action}
                {delete}
            </div>
        </div>
        """

    content = f"""
    <main class="page">
        <div class="header">
            <div>
                <h1>Administration</h1>
                <p>Manage MyCloud accounts.</p>
            </div>
        </div>

        <section class="card panel">
            <h2>Create user</h2>

            <form class="admin-grid" method="POST" action="/admin/create">
                <label class="field">
                    Username
                    <input name="username" minlength="3" required>
                </label>

                <label class="field">
                    Password
                    <input type="password" name="password" minlength="8" required>
                </label>

                <label class="field">
                    Storage (GB)
                    <input type="number" name="quota" value="5" min="0.1" step="0.1" required>
                </label>

                <button class="btn primary">Create user</button>
            </form>
        </section>

        <section class="card">
            {rows}
        </section>
    </main>
    """

    return layout(content, "Administration · MyCloud")


@app.route("/admin/create", methods=["POST"])
def admin_create():
    if not admin():
        abort(403)

    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")

    try:
        quota = float(request.form.get("quota", "5"))
    except ValueError:
        abort(400)

    if len(username) < 3 or len(password) < 8 or quota <= 0:
        abort(400)

    c = db()

    try:
        c.execute("""
            INSERT INTO users
            (username,password_hash,admin,quota,created_at)
            VALUES (?,?,0,?,?)
        """, (
            username,
            generate_password_hash(password),
            int(quota * 1024 ** 3),
            now()
        ))

        c.commit()

    except sqlite3.IntegrityError:
        c.close()
        return "Username already exists.", 409

    c.close()

    log("User created", username)

    return redirect("/admin")


@app.route("/admin/quota/<int:user_id>", methods=["POST"])
def admin_quota(user_id):
    if not admin():
        abort(403)

    try:
        quota = float(request.form.get("quota"))
    except (ValueError, TypeError):
        abort(400)

    if quota <= 0:
        abort(400)

    c = db()

    user = c.execute(
        "SELECT username FROM users WHERE id=?",
        (user_id,)
    ).fetchone()

    if not user:
        c.close()
        abort(404)

    c.execute("""
        UPDATE users
        SET quota=?
        WHERE id=?
    """, (
        int(quota * 1024 ** 3),
        user_id
    ))

    c.commit()
    c.close()

    log("Storage quota changed", user["username"])

    return redirect("/admin")


@app.route("/admin/admin/<int:user_id>", methods=["POST"])
def admin_role(user_id):
    if not admin():
        abort(403)

    if user_id == session["user_id"]:
        abort(400)

    value = request.form.get("value")

    if value not in ("0", "1"):
        abort(400)

    c = db()

    user = c.execute(
        "SELECT username FROM users WHERE id=?",
        (user_id,)
    ).fetchone()

    if not user:
        c.close()
        abort(404)

    c.execute("""
        UPDATE users
        SET admin=?
        WHERE id=?
    """, (
        int(value),
        user_id
    ))

    c.commit()
    c.close()

    action = "Administrator granted" if value == "1" else "Administrator removed"

    log(action, user["username"])

    return redirect("/admin")


@app.route("/admin/delete/<int:user_id>", methods=["POST"])
def admin_delete(user_id):
    if not admin():
        abort(403)

    if user_id == session["user_id"]:
        abort(400)

    c = db()

    user = c.execute(
        "SELECT * FROM users WHERE id=?",
        (user_id,)
    ).fetchone()

    if not user:
        c.close()
        abort(404)

    username = user["username"]

    c.execute(
        "DELETE FROM users WHERE id=?",
        (user_id,)
    )

    c.commit()
    c.close()

    directory = os.path.join(
        STORAGE,
        str(user_id)
    )

    if os.path.isdir(directory):
        shutil.rmtree(directory)

    log("User deleted", username)

    return redirect("/admin")


@app.route("/logout")
def logout():
    if logged():
        log("Logout", "Signed out")

    session.clear()

    return redirect("/login")


@app.errorhandler(413)
def too_large(error):
    return "Upload too large.", 413


init_db()


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=8000,
        debug=False
    )