
import os
import sqlite3
import uuid
from pathlib import Path
from flask import request, redirect, url_for, session, flash
from werkzeug.utils import secure_filename

import uuid
import os
import re
import json
import sqlite3
from pathlib import Path

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    flash
)

from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv

try:
    from huggingface_hub import InferenceClient
except ImportError:
    InferenceClient = None


# =========================================================
# CONFIGURATION
# =========================================================

BASE_DIR = Path(__file__).resolve().parent

load_dotenv(BASE_DIR / ".env")

HF_TOKEN = os.getenv("HF_TOKEN")

app = Flask(__name__)

app.secret_key = os.getenv(
    "FLASK_SECRET_KEY",
    "change-this-secret-key-for-local-development"
)

DATABASE = BASE_DIR / "database.db"

# Profile photos will be saved in static/uploads/
UPLOAD_FOLDER = BASE_DIR / "static" / "uploads"

ALLOWED_EXTENSIONS = {
    "png",
    "jpg",
    "jpeg",
    "webp"
}

app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024

app.config["UPLOAD_FOLDER"] = str(UPLOAD_FOLDER)

UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)


# =========================================================
# FILE VALIDATION
# =========================================================

def allowed_file(filename):
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower()
        in ALLOWED_EXTENSIONS
    )


# =========================================================
# HUGGING FACE CLIENT
# =========================================================

hf_client = None

if HF_TOKEN and InferenceClient:
    hf_client = InferenceClient(
        provider="hf-inference",
        api_key=HF_TOKEN
    )


# =========================================================
# QUESTION BANK
# =========================================================

question_bank = {
    "general": [
        "Tell me about yourself.",
        "What are your strengths?",
        "What are your weaknesses?",
        "Why should we hire you?",
        "Where do you see yourself in five years?"
    ],

    "python": [
        "What is Python?",
        "What is the difference between a list and a tuple?",
        "What are functions in Python?",
        "Explain Object-Oriented Programming.",
        "What is exception handling?"
    ],

    "technical": [
        "What is an operating system?",
        "What is a database?",
        "What is SQL?",
        "What is the difference between HTTP and HTTPS?",
        "Explain frontend and backend."
    ],

    "cloud": [
        "What is cloud computing?",
        "Explain IaaS, PaaS and SaaS.",
        "What is AWS?",
        "What is virtualization?",
        "Compare public and private cloud."
    ],

    "hr": [
        "Tell me about yourself.",
        "Why do you want to join our company?",
        "What are your strengths?",
        "What is your biggest weakness?",
        "Where do you see yourself in five years?"
    ],

    "devops": [
        "What is DevOps?",
        "What is Git?",
        "What is Docker?",
        "What is CI/CD?",
        "What is Kubernetes?"
    ]
}


# =========================================================
# DATABASE CONNECTION
# =========================================================

def get_db():
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
    return connection


# =========================================================
# CREATE DATABASE
# =========================================================

def create_database():

    with get_db() as connection:

        connection.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL
            )
        """)

        columns = connection.execute(
            "PRAGMA table_info(users)"
        ).fetchall()

        column_names = [
            column["name"] for column in columns
        ]

        # Add the photo column to existing databases
        # without deleting existing users.
        if "profile_photo" not in column_names:
            connection.execute("""
                ALTER TABLE users
                ADD COLUMN profile_photo TEXT
            """)

    UPLOAD_FOLDER.mkdir(
        parents=True,
        exist_ok=True
    )


# =========================================================
# AI QUESTION GENERATION
# =========================================================

def generate_ai_questions(category):

    fallback = question_bank.get(
        category,
        question_bank["general"]
    )

    if not hf_client:
        print("AI unavailable: using predefined questions.")
        return fallback

    prompt = f"""
Generate exactly five interview questions for the
IT interview category: {category}.

Return ONLY a JSON array of five strings.
Do not include answers.
"""

    try:

        response = hf_client.chat_completion(
            model="Qwen/Qwen2.5-7B-Instruct",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Generate professional interview questions."
                    )
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            max_tokens=500,
            temperature=0.7
        )

        result = response.choices[0].message.content.strip()

        start = result.find("[")
        end = result.rfind("]")

        if start != -1 and end > start:

            questions = json.loads(
                result[start:end + 1]
            )

            if isinstance(questions, list):

                questions = [
                    str(question).strip()
                    for question in questions
                    if isinstance(question, str)
                    and question.strip()
                ]

                if len(questions) >= 5:
                    return questions[:5]

    except Exception as error:
        print("AI QUESTION ERROR:", repr(error))

    print("Using predefined questions for:", category)

    return fallback


# =========================================================
# FALLBACK ANSWER SCORING
# =========================================================

def fallback_evaluate(question, answer):
    """
    Basic heuristic scoring when AI is unavailable.
    This is an estimate, not a substitute for AI evaluation.
    """

    if not answer or not answer.strip():
        return {
            "score": 0,
            "feedback": "No answer was provided."
        }

    words = re.findall(
        r"\b[\w+#.-]+\b",
        answer.lower()
    )

    unique_words = set(words)

    if len(words) < 3:
        return {
            "score": 10,
            "feedback": (
                "Your answer is too short. Explain the concept "
                "in more detail and provide an example."
            )
        }

    # Answer length score
    length_score = min(
        len(words) / 50,
        1.0
    ) * 50

    # Relevant words from the question
    stop_words = {
        "what", "is", "are", "the", "a", "an", "of", "and",
        "or", "to", "in", "for", "how", "why", "explain",
        "difference", "between", "tell", "me", "your",
        "you", "should", "where", "five", "years"
    }

    question_terms = {
        word
        for word in re.findall(
            r"\b[\w+#.-]+\b",
            question.lower()
        )
        if word not in stop_words and len(word) > 1
    }

    overlap = len(
        unique_words & question_terms
    )

    if question_terms:
        relevance_score = min(
            overlap / min(len(question_terms), 3),
            1.0
        ) * 30
    else:
        relevance_score = 15

    # Reward explanatory details
    detail_terms = {
        "because", "example", "such", "using", "helps",
        "allows", "means", "includes", "which", "process",
        "system", "data", "used", "works"
    }

    detail_score = min(
        len(unique_words & detail_terms) / 3,
        1.0
    ) * 20

    score = round(
        min(
            100,
            length_score + relevance_score + detail_score
        )
    )

    return {
        "score": score,
        "feedback": (
            "AI evaluation is unavailable. This score is a basic "
            "estimate based on answer length and keyword relevance. "
            "It does not verify technical correctness. Configure "
            "Hugging Face for a more meaningful AI evaluation."
        )
    }


# =========================================================
# AI ANSWER EVALUATION
# =========================================================

def evaluate_answer(question, answer):

    if not answer or not answer.strip():
        return {
            "score": 0,
            "feedback": "No answer was provided."
        }

    if not hf_client:
        print(
            "AI EVALUATION: no Hugging Face client configured."
        )
        return fallback_evaluate(question, answer)

    prompt = f"""
You are a fair and professional interview evaluator.

Interview question:
{question}

Candidate answer:
{answer}

Evaluate the answer for:
1. Technical or factual correctness
2. Relevance to the question
3. Clarity and completeness
4. Examples or practical understanding where appropriate

Give a score from 0 to 100. Do not give a high score
merely because the answer is long.

Return exactly this format:
SCORE: 75
FEEDBACK: Explain the answer's strengths and weaknesses.
"""

    try:

        response = hf_client.chat_completion(
            model="Qwen/Qwen2.5-7B-Instruct",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Evaluate interview answers fairly. "
                        "Return a numeric score from 0 to 100 "
                        "and concise, useful feedback."
                    )
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            max_tokens=250,
            temperature=0.2
        )

        result = (
            response.choices[0].message.content or ""
        ).strip()

        print(
            "AI EVALUATION RESPONSE:",
            repr(result)
        )

        match = re.search(
            r"\bSCORE\s*:\s*(\d{1,3})(?:\s*/\s*100)?",
            result,
            re.IGNORECASE
        )

        if not match:
            print("AI returned no readable score.")
            return fallback_evaluate(question, answer)

        score = max(
            0,
            min(100, int(match.group(1)))
        )

        feedback_match = re.search(
            r"\bFEEDBACK\s*:\s*(.*)",
            result,
            re.IGNORECASE | re.DOTALL
        )

        feedback = (
            feedback_match.group(1).strip()
            if feedback_match
            else result
        )

        return {
            "score": score,
            "feedback": feedback
        }

    except Exception as error:

        print(
            "AI EVALUATION ERROR:",
            repr(error)
        )

        return fallback_evaluate(question, answer)


# =========================================================
# HOME
# =========================================================

@app.route("/")
def home():

    if "user_id" in session:
        return redirect(url_for("dashboard"))

    return redirect(url_for("login"))


# =========================================================
# REGISTER
# =========================================================

@app.route("/register", methods=["GET", "POST"])
def register():

    if request.method == "POST":

        name = request.form.get(
            "name", ""
        ).strip()

        email = request.form.get(
            "email", ""
        ).strip().lower()

        password = request.form.get(
            "password", ""
        )

        if not name or not email or not password:
            flash("Please fill all fields.")
            return redirect(url_for("register"))

        if len(password) < 8:
            flash(
                "Password must be at least 8 characters."
            )
            return redirect(url_for("register"))

        connection = get_db()

        try:

            connection.execute(
                """
                INSERT INTO users (name, email, password)
                VALUES (?, ?, ?)
                """,
                (
                    name,
                    email,
                    generate_password_hash(password)
                )
            )

            connection.commit()

            flash(
                "Registration successful. Please login."
            )

            return redirect(url_for("login"))

        except sqlite3.IntegrityError:

            flash(
                "This email is already registered."
            )

            return redirect(url_for("register"))

        except sqlite3.Error as error:

            print(
                "REGISTRATION DATABASE ERROR:",
                repr(error)
            )

            flash(
                "Database error. Please try again."
            )

            return redirect(url_for("register"))

        finally:
            connection.close()

    return render_template("register.html")


# =========================================================
# LOGIN
# =========================================================

@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        email = request.form.get(
            "email", ""
        ).strip().lower()

        password = request.form.get(
            "password", ""
        )

        connection = get_db()

        try:

            user = connection.execute(
                """
                SELECT *
                FROM users
                WHERE email = ?
                """,
                (email,)
            ).fetchone()

        finally:
            connection.close()

        if user and check_password_hash(
            user["password"],
            password
        ):

            session["user_id"] = user["id"]
            session["name"] = user["name"]
            session["email"] = user["email"]

            return redirect(
                url_for("dashboard")
            )

        flash("Invalid email or password.")

    return render_template("login.html")


# =========================================================
# LOGOUT
# =========================================================

@app.route("/logout")
def logout():

    session.clear()

    return redirect(
        url_for("login")
    )


# =========================================================
# DASHBOARD
# =========================================================

@app.route("/dashboard")
def dashboard():

    if "user_id" not in session:
        return redirect(url_for("login"))

    return render_template(
        "dashboard.html",
        name=session.get("name", "User")
    )


# =========================================================
# PROFILE
# =========================================================

@app.route("/profile")
def profile():

    if "user_id" not in session:
        return redirect(url_for("login"))

    connection = get_db()

    try:

        user = connection.execute(
            """
            SELECT id, name, email, profile_photo
            FROM users
            WHERE id = ?
            """,
            (session["user_id"],)
        ).fetchone()

    finally:
        connection.close()

    if user is None:
        session.clear()
        return redirect(url_for("login"))

    return render_template(
        "profile.html",
        user=user
    )


# =========================================================
# UPLOAD PROFILE PHOTO
# =========================================================

@app.route("/upload-photo", methods=["POST"])
def upload_photo():

    if "user_id" not in session:
        return redirect(url_for("login"))

    photo = request.files.get("photo")

    if not photo or not photo.filename:
        flash("Please select a photo first.")
        return redirect(url_for("profile"))

    if not allowed_file(photo.filename):
        flash(
            "Invalid file type. Please upload PNG, JPG, "
            "JPEG, or WEBP."
        )
        return redirect(url_for("profile"))

    # Generate a unique filename to avoid collisions.
    extension = photo.filename.rsplit(
        ".", 1
    )[1].lower()

    filename = secure_filename(
        f"{uuid.uuid4().hex}.{extension}"
    )

    filepath = UPLOAD_FOLDER / filename

    try:

        UPLOAD_FOLDER.mkdir(
            parents=True,
            exist_ok=True
        )

        # Save the uploaded image.
        photo.save(str(filepath))

        connection = get_db()

        try:

            cursor = connection.execute(
                """
                UPDATE users
                SET profile_photo = ?
                WHERE id = ?
                """,
                (
                    filename,
                    session["user_id"]
                )
            )

            connection.commit()

            if cursor.rowcount == 0:
                filepath.unlink(missing_ok=True)
                flash("User account not found.")
                return redirect(url_for("login"))

        finally:
            connection.close()

        flash(
            "Profile photo uploaded successfully!"
        )

    except (OSError, sqlite3.Error) as error:

        # Remove the new file if the database update fails.
        try:
            filepath.unlink(missing_ok=True)
        except OSError:
            pass

        print(
            "PROFILE PHOTO UPLOAD ERROR:",
            repr(error)
        )

        flash(
            "Could not save the photo. Please try again."
        )

    return redirect(url_for("profile"))


# =========================================================
# CATEGORY
# =========================================================

@app.route("/category")
def category():

    if "user_id" not in session:
        return redirect(url_for("login"))

    return render_template("category.html")


# =========================================================
# INTERVIEW
# =========================================================

@app.route("/interview", methods=["GET", "POST"])
def interview():

    if "user_id" not in session:
        return redirect(url_for("login"))

    if request.method == "GET":

        category_name = request.args.get(
            "category"
        )

        if category_name:

            category_name = category_name.lower()

            if category_name not in question_bank:
                category_name = "general"

            session["category"] = category_name

            session["questions"] = generate_ai_questions(
                category_name
            )

            session["current_question"] = 0
            session["answers"] = []
            session["scores"] = []
            session["feedback"] = []

    else:

        category_name = request.form.get(
            "category",
            session.get("category", "general")
        ).lower()

        if category_name not in question_bank:
            category_name = "general"

        session["category"] = category_name

        session["questions"] = generate_ai_questions(
            category_name
        )

        session["current_question"] = 0
        session["answers"] = []
        session["scores"] = []
        session["feedback"] = []

    questions = session.get(
        "questions", []
    )

    if not questions:
        return redirect(url_for("category"))

    current = session.get(
        "current_question", 0
    )

    if current >= len(questions):
        return redirect(url_for("result"))

    return render_template(
        "interview.html",
        question=questions[current],
        question_number=current + 1,
        total_questions=len(questions),
        category=session.get(
            "category", "general"
        )
    )


# =========================================================
# SUBMIT ANSWER
# =========================================================

@app.route("/submit_answer", methods=["POST"])
def submit_answer():

    if "user_id" not in session:
        return redirect(url_for("login"))

    questions = session.get(
        "questions", []
    )

    current = session.get(
        "current_question", 0
    )

    if not questions:
        return redirect(url_for("category"))

    if current >= len(questions):
        return redirect(url_for("result"))

    question = questions[current]

    answer = request.form.get(
        "answer", ""
    ).strip()

    evaluation = evaluate_answer(
        question,
        answer
    )

    answers = session.get(
        "answers", []
    )

    answers.append(answer)
    session["answers"] = answers

    scores = session.get(
        "scores", []
    )

    scores.append(evaluation["score"])
    session["scores"] = scores

    feedback = session.get(
        "feedback", []
    )

    feedback.append(evaluation["feedback"])
    session["feedback"] = feedback

    session["current_question"] = current + 1

    if session["current_question"] < len(questions):
        return redirect(url_for("interview"))

    return redirect(url_for("result"))


# =========================================================
# RESULTS
# =========================================================

@app.route("/result")
def result():

    if "user_id" not in session:
        return redirect(url_for("login"))

    questions = session.get(
        "questions", []
    )

    answers = session.get(
        "answers", []
    )

    scores = session.get(
        "scores", []
    )

    feedback = session.get(
        "feedback", []
    )

    percentage = (
        round(sum(scores) / len(scores))
        if scores
        else 0
    )

    if percentage >= 80:

        status = "Excellent"

        message = (
            "Excellent performance! Keep up the great work."
        )

    elif percentage >= 60:

        status = "Good"

        message = (
            "Good performance! Keep practicing."
        )

    else:

        status = "Needs Improvement"

        message = (
            "Keep practicing your interview skills "
            "and try again."
        )

    evaluations = []

    for i, answer in enumerate(answers):

        evaluations.append({
            "question": (
                questions[i]
                if i < len(questions)
                else ""
            ),

            "answer": answer,

            "score": (
                scores[i]
                if i < len(scores)
                else 0
            ),

            "feedback": (
                feedback[i]
                if i < len(feedback)
                else ""
            )
        })

    return render_template(
        "result.html",
        percentage=percentage,
        status=status,
        message=message,
        answered=len(answers),
        total=len(questions),
        category=session.get(
            "category", "general"
        ),
        evaluations=evaluations
    )


# =========================================================
# HANDLE LARGE UPLOAD ERRORS
# =========================================================

@app.errorhandler(413)
def file_too_large(error):

    flash(
        "The image is too large. Maximum allowed size is 5 MB."
    )

    return redirect(url_for("profile"))


# =========================================================
# START APPLICATION
# =========================================================

if __name__ == "__main__":

    create_database()

    print("=" * 50)
    print("AI INTERVIEW SYSTEM")
    print("Database initialized.")
    print(
        "Hugging Face client available:",
        bool(hf_client)
    )
    print(
        "Profile photo folder:",
        UPLOAD_FOLDER
    )
    print(
        "Open http://127.0.0.1:5000 in your browser."
    )
    print("=" * 50)

    app.run(
        debug=True,
        host="127.0.0.1",
        port=5000
    )
