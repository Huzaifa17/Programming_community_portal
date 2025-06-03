from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
import re
from utils import make_links_clickable
from pymongo import MongoClient
from bson.objectid import ObjectId
from dotenv import load_dotenv
import os
import bleach
from werkzeug.utils import secure_filename
from flask_wtf import FlaskForm
from wtforms import TextAreaField, FileField, SubmitField
from wtforms.validators import DataRequired
import random
import smtplib
from email.mime.text import MIMEText
from datetime import datetime, timedelta
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail
from collections import defaultdict
import secrets
from datetime import datetime
from flask import Flask
import humanize

class CommentForm(FlaskForm):
    comment = TextAreaField('Comment', validators=[DataRequired()])
    attachments = FileField('Attachments')
    submit = SubmitField('Add Comment')


client = MongoClient(os.getenv('MONGO_URI'))
db = client.flask_db



result = db.notifications.update_many(
    { 'type': { '$exists': False } },
    { '$set': { 'type': 'general' } }
)


load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY')



# Repeat code again
client = MongoClient(os.getenv('MONGO_URI'))
db = client.flask_db
users = db.users
posts = db.posts
notifications = db.notifications
comments = db.comments



posts.update_many(
    { 'pinned_timestamp': { '$exists': False } },
    { '$set': { 'pinned_timestamp': None } }
)


posts.update_many(
    { 'pinned': True, 'pinned_timestamp': None },
    { '$set': { 'pinned_timestamp': datetime.now() } }
)


if users.count_documents({'username': 'Admin'}) == 0:
    users.insert_one({'username': 'Admin', 'email': 'it20017@mbstu.ac.bd', 'password': 'Admin123$', 'role': 'admin'})


UPLOAD_FOLDER = 'static/uploads'
ALLOWED_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg', 'doc', 'docx'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS



def is_admin():
    return 'username' in session and users.find_one({'username': session['username'], 'role': 'admin'})


def is_moderator():
    return 'username' in session and users.find_one({'username': session['username'], 'role': 'moderator'})



def make_links_clickable(text):
    """
    Convert URLs in text to clickable links (colored blue and opening in a new tab).
    """
    if not text:
        return text
    url_pattern = re.compile(r'https?://\S+')
    return url_pattern.sub(r'<a href="\g<0>" target="_blank" style="color: blue;">\g<0></a>', text)



@app.context_processor
def inject_notification_utils():
    def notification_link(notification):
        if notification['type'] == 'moderator':
            user = users.find_one({'_id': notification['target_user_id']})
            return url_for('profile', username=user['username']) if user else '#'
        elif notification['type'] in ['post_approved', 'post_updated', 'post_deleted']:
            post = posts.find_one({'_id': notification['target_post_id']})
            if notification['type'] == 'post_updated':
                return url_for('edit_post', post_id=post['_id']) if post else '#'
            elif notification['type'] == 'post_deleted':
                return url_for('home')
            return url_for('view_topic', post_id=post['_id']) if post else '#'
        return '#'
    return {'notification_link': notification_link}




@app.template_filter('relative_time')
def relative_time_filter(dt):
    if not dt:
        return ""
    now = datetime.now()
    diff = now - dt
    return humanize.naturaltime(dt)


@app.context_processor
def utility_processor():
    unseen_count = 0
    if 'username' in session:
        unseen_count = notifications.count_documents({'seen': False})
    return dict(
        is_admin=is_admin,
        is_moderator=is_moderator,
        make_links_clickable=make_links_clickable,
        unseen_count=unseen_count 
    )


@app.before_request
def update_last_active():
    if 'username' in session:
        users.update_one(
            {'username': session['username']},
            {'$set': {'last_active': datetime.utcnow()}}
        )



def is_strong_password(password):
    """
    Check if the password meets the following criteria:
    - At least 8 characters long.
    - Contains at least one uppercase letter.
    - Contains at least one lowercase letter.
    - Contains at least one digit.
    - Contains at least one special character.
    """
    if len(password) < 8:
        return False
    if not re.search(r'[A-Z]', password):
        return False
    if not re.search(r'[a-z]', password):
        return False
    if not re.search(r'[0-9]', password):
        return False
    if not re.search(r'[!@#$%^&*()]', password):
        return False
    return True



def get_top_level_parent(comment_id):
    """
    Find the top-level parent comment of a reply.
    """
    comment = comments.find_one({'_id': ObjectId(comment_id)})
    while comment and comment.get('parent_comment_id'):
        parent_id = comment['parent_comment_id']
        comment = comments.find_one({'_id': parent_id})
    return comment



def calculate_comment_page(post_id, comment_timestamp, per_page=5):
    """
    Calculate the page number for a comment or reply based on its timestamp.
    """
    count_before = comments.count_documents({
        'post_id': ObjectId(post_id),
        'parent_comment_id': None, 
        'timestamp': {'$lt': comment_timestamp}
    })
    return (count_before // per_page) + 1



def add_notification(notification_type, message, target_user=None, target_post=None, target_comment=None):
    """Add a notification with contextual linking"""
    sanitized_message = bleach.clean(message, tags=['a'], attributes={'a': ['href']})
    
    notification = {
        'type': notification_type,
        'message': sanitized_message,
        'timestamp': datetime.now(),
        'seen': False
    }
    
    if target_user:
        notification['target_user_id'] = target_user['_id']
        notification['link'] = url_for('profile', username=target_user['username'])
    
    if target_post:
        notification['target_post_id'] = target_post['_id']
        notification['link'] = url_for('view_topic', post_id=str(target_post['_id']))
    
    if target_comment:
        if target_comment.get('parent_comment_id'):
            top_level_parent = get_top_level_parent(target_comment['parent_comment_id'])
        else:
            top_level_parent = target_comment
        
        page_number = calculate_comment_page(top_level_parent['post_id'], top_level_parent['timestamp'])
        notification['link'] = url_for('view_topic', post_id=str(target_comment['post_id']), page=page_number) + f"#comment-{target_comment['_id']}"
    
    notifications.insert_one(notification)




def fetch_comments(post_id, parent_comment_id=None, skip=0, limit=5):
    """
    Recursively fetch comments and replies for a post with pagination.
    """
    query = {'post_id': ObjectId(post_id), 'parent_comment_id': parent_comment_id}
    comments_list = list(comments.find(query).sort('timestamp', 1).skip(skip).limit(limit))

    for comment in comments_list:
        comment['replies'] = fetch_comments(post_id, comment['_id'])
    return comments_list




@app.template_filter('datetimeformat')
def datetimeformat(value, format='%Y-%m-%d %H:%M'):
    return value.strftime(format)



otp_storage = {}

def send_otp_email(email, otp):
    try:
        # Configure SendGrid API key
        sg = SendGridAPIClient(os.getenv('sendgrid_api_key'))

        # Create the email message
        message = Mail(
            from_email="huzaifareed100@gmail.com",
            to_emails=email,
            subject="Your OTP for Password Reset",
            html_content=f"Your OTP is: <strong>{otp}</strong>. It will expire in 5 minutes."
        )

        # Send the email
        response = sg.send(message)
        print(f"Email sent. Status Code: {response.status_code}")
        return True
    except Exception as e:
        print(f"Error sending email: {e}")
        return False
    

verification_tokens = {}

def generate_verification_token():
    """Generate a secure random token for email verification."""
    return secrets.token_urlsafe(32)

def send_verification_email(email, token):
    """Send a verification email using SendGrid."""
    try:
        sg = SendGridAPIClient(os.getenv('SENDGRID_API_KEY'))
        verification_url = f"{os.getenv('VERIFICATION_BASE_URL')}/verify_email/{token}"
        message = Mail(
            from_email="huzaifareed100@gmail.com",
            to_emails=email,
            subject="Verify Your Email Address",
            html_content=f"""
                <h3>Welcome to the Programming Community!</h3>
                <p>Please verify your email address by clicking the link below:</p>
                <p><a href="{verification_url}">Verify Email</a></p>
                <p>If you did not create an account, please ignore this email.</p>
            """
        )

        response = sg.send(message)
        print(f"Verification email sent. Status Code: {response.status_code}")
        return True
    except Exception as e:
        print(f"Error sending verification email: {e}")
        return False
    

@app.route('/check_login')
def check_login():
    if 'username' in session:
        return f"Logged in as {session['username']}"
    else:
        return "Not logged in"


@app.route('/verify_email/<token>')
def verify_email(token):
    """Verify the user's email using the token."""
    if token in verification_tokens and datetime.now() <= verification_tokens[token]['expiration']:
        email = verification_tokens[token]['email']
        
        if users.find_one({'email': email, 'verified': True}):
            flash('Email already verified. Please login.', 'info')
            return redirect(url_for('login'))

        users.insert_one({
            'username': verification_tokens[token]['username'],
            'email': email,
            'password': verification_tokens[token]['password'],  # Hash this in production
            'role': 'user',
            'verified': True,  # Mark as verified
            'created_at': datetime.now()
        })
        
        del verification_tokens[token]
        
        flash('Email verified successfully! You can now log in.', 'success')
        return redirect(url_for('login'))
    else:
        flash('Invalid or expired verification link. Please sign up again.', 'error')
        return redirect(url_for('signup'))



# here it goes fronm the top 
# 11111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111

@app.route('/routes')
def list_routes():
    import urllib
    output = []
    for rule in app.url_map.iter_rules():
        methods = ','.join(sorted(rule.methods))
        line = urllib.parse.unquote(f"{rule.endpoint}: {rule} ({methods})")
        output.append(line)
    return "<br>".join(sorted(output))



@app.route('/pin_post/<post_id>')
def pin_post(post_id):
    if not is_moderator():
        flash('Permission denied. Only moderators can pin posts.', 'error')
        return redirect(url_for('view_topic', post_id=post_id))
    
    post = posts.find_one({'_id': ObjectId(post_id)})
    if not post:
        flash('Post not found.', 'error')
        return redirect(url_for('home'))
    
    posts.update_one(
        {'_id': ObjectId(post_id)},
        {'$set': {'pinned': True, 'pinned_timestamp': datetime.now()}}
    )
    
    add_notification(
        'post_pinned',
        f"📌 {session['username']} pinned the post: {post['title']}",
        target_post=post
    )
    
    flash('Post pinned successfully!', 'success')
    return redirect(url_for('view_topic', post_id=post_id))




@app.route('/unpin_post/<post_id>')
def unpin_post(post_id):
    if not is_moderator():
        flash('Permission denied. Only moderators can unpin posts.', 'error')
        return redirect(url_for('view_topic', post_id=post_id))
    
    post = posts.find_one({'_id': ObjectId(post_id)})
    if not post:
        flash('Post not found.', 'error')
        return redirect(url_for('home'))
    
    posts.update_one(
        {'_id': ObjectId(post_id)},
        {'$set': {'pinned': False, 'pinned_timestamp': None}}
    )
    
    add_notification(
        'post_unpinned',
        f"📌 {session['username']} unpinned the post: {post['title']}",
        target_post=post
    )
    
    flash('Post unpinned successfully!', 'success')
    return redirect(url_for('view_topic', post_id=post_id))





@app.route('/forgot_password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form['email']
        user = users.find_one({'email': email})
        if user:
            otp = str(random.randint(100000, 999999))
            otp_expiration = datetime.now() + timedelta(minutes=5)

            otp_storage[email] = {
                'otp': otp,
                'expiration': otp_expiration
            }

            if send_otp_email(email, otp):
                flash('OTP sent to your email. Please check your inbox.', 'success')
                return redirect(url_for('verify_otp', email=email))
            else:
                flash('Failed to send OTP. Please try again.', 'error')
        else:
            flash('Email not found. Please enter a valid email.', 'error')
    return render_template('forgot_password.html')



# /////////





@app.route('/verify_otp/<email>', methods=['GET', 'POST'])
def verify_otp(email):
    if request.method == 'POST':
        user_otp = request.form['otp']
        stored_otp_data = otp_storage.get(email)

        if stored_otp_data and datetime.now() <= stored_otp_data['expiration']:
            if user_otp == stored_otp_data['otp']:
                flash('OTP verified. You can now reset your password.', 'success')
                return redirect(url_for('reset_password', email=email))
            else:
                flash('Invalid OTP. Please try again.', 'error')
        else:
            flash('OTP expired. Please request a new OTP.', 'error')
    
    stored_otp_data = otp_storage.get(email)
    if stored_otp_data:
        expiration_time = stored_otp_data['expiration'].strftime('%Y-%m-%d %H:%M:%S')
    else:
        expiration_time = None

    return render_template('verify_otp.html', email=email, expiration_time=expiration_time)




@app.route('/reset_password/<email>', methods=['GET', 'POST'])
def reset_password(email):
    if request.method == 'POST':
        new_password = request.form['new_password']
        confirm_password = request.form['confirm_password']

        if new_password != confirm_password:
            flash('Passwords do not match. Please try again.', 'error')
            return redirect(url_for('reset_password', email=email))

        if not is_strong_password(new_password):
            flash(
                'Password is too weak. It must be at least 8 characters long, '
                'contain at least one uppercase letter, one lowercase letter, '
                'one digit, and one special character (!@#$%^&*()).',
                'error'
            )
            return redirect(url_for('reset_password', email=email))

        users.update_one({'email': email}, {'$set': {'password': new_password}})
        flash('Password reset successfully. Please login with your new password.', 'success')
        return redirect(url_for('login'))

    return render_template('reset_password.html', email=email)





@app.route('/change_password', methods=['GET', 'POST'])
def change_password():
    if 'username' not in session:
        flash('Please log in to change your password.', 'error')
        return redirect(url_for('login'))

    if request.method == 'POST':
        current_password = request.form['current_password']
        new_password = request.form['new_password']
        confirm_password = request.form['confirm_password']

        user = users.find_one({'username': session['username']})

        if user['password'] != current_password:
            flash('Current password is incorrect.', 'error')
            return redirect(url_for('change_password'))

        if new_password != confirm_password:
            flash('New password and confirmation do not match.', 'error')
            return redirect(url_for('change_password'))

        if not is_strong_password(new_password):
            flash(
                'Password is too weak. It must be at least 8 characters long, '
                'contain at least one uppercase letter, one lowercase letter, '
                'one digit, and one special character (!@#$%^&*()).',
                'error'
            )
            return redirect(url_for('change_password'))

        users.update_one({'username': session['username']}, {'$set': {'password': new_password}})
        flash('Password changed successfully!', 'success')
        return redirect(url_for('profile', username=session['username']))

    return render_template('change_password.html')






@app.route('/')
def index():
    if 'username' in session:
        return redirect(url_for('home'))
    return redirect(url_for('login'))




@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'username' in session:
        flash('You are already logged in.', 'info')
        return redirect(url_for('home'))

    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        user = users.find_one({'email': email, 'password': password})

        if user:
            if not user.get('verified', False):
                flash('Please verify your email address before logging in.', 'error')
                return redirect(url_for('login'))
            
            session['username'] = user['username']
            return redirect(url_for('home'))
        else:
            flash('Invalid email or password', 'error')

    return render_template('login.html')




@app.route('/edit_post/<post_id>', methods=['GET', 'POST'])
def edit_post(post_id):
    if 'username' not in session:
        return redirect(url_for('login'))
    
    post = posts.find_one({'_id': ObjectId(post_id)})
    if not post:
        flash('Post not found', 'error')
        return redirect(url_for('home'))
    
    if post['username'] != session['username']:
        flash('You do not have permission to edit this post.', 'error')
        return redirect(url_for('home'))
    
    if request.method == 'POST':
        title = request.form['title']
        content = request.form['content']
        attachments = request.files.getlist('attachments')
        
        attachment_urls = post.get('attachment_urls', [])
        for attachment in attachments:
            if attachment and allowed_file(attachment.filename):
                filename = secure_filename(attachment.filename)
                attachment_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                attachment.save(attachment_path)
                attachment_urls.append(url_for('static', filename=f'uploads/{filename}'))
        
        posts.update_one(
            {'_id': ObjectId(post_id)},
            {
                '$set': {
                    'title': title,
                    'content': content,
                    'attachment_urls': attachment_urls
                }
            }
        )
        
        add_notification(
            'post_updated',
            f"📝 {session['username']} updated the post: {title}",
            target_post=post
        )
        
        flash('Post updated successfully!', 'success')
        return redirect(url_for('profile', username=session['username']))
    
    return render_template('edit_post.html', post=post)




@app.route('/delete_post/<post_id>')
def delete_post(post_id):
    if 'username' not in session:
        return redirect(url_for('login'))
    
    post = posts.find_one({'_id': ObjectId(post_id)})
    if not post:
        flash('Post not found', 'error')
        return redirect(url_for('home'))
    
    if post['username'] != session['username']:
        flash('You do not have permission to delete this post.', 'error')
        return redirect(url_for('home'))
    
    posts.delete_one({'_id': post['_id']})
    
    add_notification(
        'post_deleted',
        f"🗑️ {post['username']}'s post '{post['title']}' was deleted",
        target_post=post
    )
    
    flash('Post deleted successfully!', 'success')
    return redirect(url_for('home'))



# @app.route('/signup', methods=['GET', 'POST'])
# def signup():
#     # Check if the user is already logged in
#     if 'username' in session:
#         flash('You are already logged in.', 'info')
#         return redirect(url_for('home'))  # Redirect to home if logged in

#     if request.method == 'POST':
#         username = request.form['username']
#         email = request.form['email']
#         password = request.form['password']
#         confirm_password = request.form['confirm_password']

#         # Check if the email already exists
#         if users.find_one({'email': email}):
#             flash('Email already exists', 'error')
#             return render_template('signup.html')

#         # Validate password strength
#         if not is_strong_password(password):
#             flash(
#                 'Password is too weak. It must be at least 8 characters long, '
#                 'contain at least one uppercase letter, one lowercase letter, '
#                 'one digit, and one special character (!@#$%^&*()).',
#                 'error'
#             )
#             return render_template('signup.html')

#         # Validate password confirmation
#         if password != confirm_password:
#             flash('Passwords do not match. Please try again.', 'error')
#             return render_template('signup.html')

#         # Generate a verification token
#         token = generate_verification_token()
#         verification_tokens[token] = {
#             'email': email,
#             'expiration': datetime.now() + timedelta(hours=24)  # Token expires in 24 hours
#         }

#         # Create the user account (initially unverified)
#         users.insert_one({
#             'username': username,
#             'email': email,
#             'password': password,  # In production, hash the password before storing
#             'role': 'user',
#             'verified': False  # Mark the user as unverified
#         })

#         # Send the verification email
#         if send_verification_email(email, token):
#             flash('Account created successfully! Please check your email to verify your account.', 'success')
#         else:
#             flash('Failed to send verification email. Please try again.', 'error')

#         return redirect(url_for('login'))

#     return render_template('signup.html')



@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if 'username' in session:
        flash('You are already logged in.', 'info')
        return redirect(url_for('home'))

    if request.method == 'POST':
        username = request.form['username']
        email = request.form['email']
        password = request.form['password']
        confirm_password = request.form['confirm_password']

        if users.find_one({'email': email, 'verified': True}):
            flash('Email already exists', 'error')
            return render_template('signup.html')

        if not is_strong_password(password):
            flash(
                'Password is too weak. It must be at least 8 characters long, '
                'contain at least one uppercase letter, one lowercase letter, '
                'one digit, and one special character (!@#$%^&*()).',
                'error'
            )
            return render_template('signup.html')

        if password != confirm_password:
            flash('Passwords do not match. Please try again.', 'error')
            return render_template('signup.html')

        token = generate_verification_token()
        verification_tokens[token] = {
            'email': email,
            'username': username,
            'password': password,
            'expiration': datetime.now() + timedelta(hours=24)
        }

        if send_verification_email(email, token):
            flash('Verification email sent! Please check your inbox to complete registration.', 'success')
            return redirect(url_for('login'))
        else:
            flash('Failed to send verification email. Please try again.', 'error')
            return render_template('signup.html')

    return render_template('signup.html')




@app.route('/home')
def home():
    if 'username' not in session:
        return redirect(url_for('login'))
    
    page = request.args.get('page', 1, type=int)
    per_page = 10
    skip = (page - 1) * per_page

    all_posts = list(posts.find({'status': 'approved'}))

    pinned_posts = [post for post in all_posts if post.get('pinned', False)]
    unpinned_posts = [post for post in all_posts if not post.get('pinned', False)]
    pinned_posts.sort(key=lambda x: x.get('pinned_timestamp', datetime.min), reverse=True)

    for post in unpinned_posts:
        contribution = post.get('upvotes', 0) - post.get('downvotes', 0)
        post['contribution'] = contribution

    unpinned_posts.sort(key=lambda x: x['contribution'], reverse=True)
    sorted_posts = pinned_posts + unpinned_posts

    paginated_posts = sorted_posts[skip:skip + per_page]
    total_posts = len(sorted_posts)

    return render_template(
        'home.html',
        posts=paginated_posts,
        page=page,
        per_page=per_page,
        total_posts=total_posts
    )



@app.route('/create_post', methods=['GET', 'POST'])
def create_post():
    if 'username' not in session:
        return redirect(url_for('login'))
    
    if request.method == 'POST':
        title = request.form['title']
        content = request.form['content']
        attachments = request.files.getlist('attachments')
        
        attachment_urls = []
        for attachment in attachments:
            if attachment and allowed_file(attachment.filename):
                filename = secure_filename(attachment.filename)
                attachment_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                attachment.save(attachment_path)
                attachment_urls.append(url_for('static', filename=f'uploads/{filename}'))
        
        user_posts = posts.find({'username': session['username']})
        total_contribution = sum(post.get('upvotes', 0) - post.get('downvotes', 0) for post in user_posts)
        
        status = 'approved' if total_contribution >= 50 else 'pending'
        
        post = {
            'title': title,
            'content': content,
            'username': session['username'],
            'upvotes': 0,
            'downvotes': 0,
            'upvoted_by': [],
            'downvoted_by': [],
            'status': status,
            'attachment_urls': attachment_urls,
            'timestamp': datetime.now(),
            'pinned': False
        }
        inserted_post = posts.insert_one(post)
        if status == 'approved':
            add_notification(
                'post_approved',
                f"📝 New post by {session['username']}: '{title}'",
                target_post=post
            )
        
        flash(f'Post created successfully! Status: {status}.', 'success')
        return redirect(url_for('home'))
    
    return render_template('create_post.html')



@app.route('/upvote/<post_id>')
def upvote(post_id):
    if 'username' not in session:
        return redirect(url_for('login'))

    post = posts.find_one({'_id': ObjectId(post_id)})
    if not post:
        flash('Post not found', 'error')
        return redirect(url_for('home'))

    if session['username'] in post.get('upvoted_by', []):
        flash('You have already upvoted this post.', 'error')
    else:
        posts.update_one(
            {'_id': ObjectId(post_id)},
            {
                '$inc': {'upvotes': 1},
                '$push': {'upvoted_by': session['username']}
            }
        )
        flash('Post upvoted!', 'success')

    return redirect(url_for('view_topic', post_id=post_id))




@app.route('/downvote/<post_id>')
def downvote(post_id):
    if 'username' not in session:
        return redirect(url_for('login'))

    post = posts.find_one({'_id': ObjectId(post_id)})
    if not post:
        flash('Post not found', 'error')
        return redirect(url_for('home'))

    if session['username'] in post.get('downvoted_by', []):
        flash('You have already downvoted this post.', 'error')
    else:
        posts.update_one(
            {'_id': ObjectId(post_id)},
            {
                '$inc': {'downvotes': 1},
                '$push': {'downvoted_by': session['username']}
            }
        )
        flash('Post downvoted!', 'success')

    return redirect(url_for('view_topic', post_id=post_id))





@app.route('/mark_notification_seen/<notification_id>')
def mark_notification_seen(notification_id):
    if 'username' not in session:
        return redirect(url_for('login'))
    
    notifications.update_one(
        {'_id': ObjectId(notification_id)},
        {'$set': {'seen': True}}
    )
    
    redirect_url = request.args.get('redirect_url', url_for('home'))
    return redirect(redirect_url)





@app.route('/add_comment/<post_id>', methods=['POST'])
def add_comment(post_id):
    if 'username' not in session:
        return redirect(url_for('login'))
    
    comment_text = request.form['comment']
    parent_comment_id = request.form.get('parent_comment_id')
    attachments = request.files.getlist('attachments')
    
    attachment_urls = []
    for attachment in attachments:
        if attachment and allowed_file(attachment.filename):
            filename = secure_filename(attachment.filename)
            attachment_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            attachment.save(attachment_path)
            attachment_urls.append(url_for('static', filename=f'uploads/{filename}'))
    
    comment = {
        'post_id': ObjectId(post_id),
        'username': session['username'],
        'comment': comment_text,
        'attachment_urls': attachment_urls,
        'parent_comment_id': ObjectId(parent_comment_id) if parent_comment_id else None,
        'timestamp': datetime.now()
    }
    inserted_comment = comments.insert_one(comment)
    
    if parent_comment_id:
        top_level_parent = get_top_level_parent(parent_comment_id)
    else:
        top_level_parent = comment
    
    page_number = calculate_comment_page(top_level_parent['post_id'], top_level_parent['timestamp'])
    
    post = posts.find_one({'_id': ObjectId(post_id)})
    if post:
        add_notification(
            'comment',
            f"{session['username']} commented on the post: {post['title']}",
            target_post=post,
            target_comment=comment
        )
    
    flash('Comment added successfully!', 'success')
    return redirect(url_for('view_topic', post_id=post_id, page=page_number) + f"#comment-{inserted_comment.inserted_id}")




@app.route('/view_topic/<post_id>')
def view_topic(post_id):
    if 'username' not in session:
        return redirect(url_for('login'))
    
    post = posts.find_one({'_id': ObjectId(post_id)})
    if not post:
        flash('Post not found', 'error')
        return redirect(url_for('home'))
    
    if post['status'] != 'approved' and session['username'] != post['username'] and not is_moderator() and not is_admin():
        flash('You do not have permission to view this post.', 'error')
        return redirect(url_for('home'))
    
    author = users.find_one({'username': post['username']})
    if not author:
        flash('Author not found', 'error')
        return redirect(url_for('home'))
    
    page = request.args.get('page', 1, type=int)
    per_page = 5
    skip = (page - 1) * per_page

    post_comments = fetch_comments(post_id, skip=skip, limit=per_page)
    total_comments = comments.count_documents({'post_id': ObjectId(post_id), 'parent_comment_id': None})
    total_contribution = post.get('upvotes', 0) - post.get('downvotes', 0)
    form = CommentForm()
    
    return render_template(
        'view_topic.html',
        post=post,
        author=author,
        comments=post_comments,
        total_contribution=total_contribution,
        page=page,
        per_page=per_page,
        total_comments=total_comments,
        form=form
    )




@app.route('/post/<post_id>/delete_attachment', methods=['POST'])
def delete_attachment(post_id):
    if request.method == 'POST':
        filename = request.json.get('filename')
        post = posts.find_one({'_id': ObjectId(post_id)})
        if post and post['username'] == session['username']:
            posts.update_one(
                {'_id': ObjectId(post_id)},
                {'$pull': {'attachments': {'filename': filename}}}
            )
            return jsonify({'success': True})
    return jsonify({'success': False})


@app.route('/profile/<username>')
def profile(username):
    if 'username' not in session:
        return redirect(url_for('login'))
    
    user = users.find_one({'username': username})
    if not user:
        flash('User not found', 'error')
        return redirect(url_for('home'))
    
    # Pagination logic for posts
    page = request.args.get('page', 1, type=int)  # Get the current page number
    per_page = 5  # Number of posts per page
    skip = (page - 1) * per_page

    if session['username'] == username or is_moderator() or is_admin():
        user_posts = list(posts.find({'username': username}).skip(skip).limit(per_page))
    else:
        user_posts = list(posts.find({'username': username, 'status': 'approved'}).skip(skip).limit(per_page))
    
    if session['username'] == username or is_moderator() or is_admin():
        total_posts = posts.count_documents({'username': username})
    else:
        total_posts = posts.count_documents({'username': username, 'status': 'approved'})
    
    total_contribution = 0
    total_upvotes = 0
    total_downvotes = 0
    for post in user_posts:
        total_contribution += post.get('upvotes', 0) - post.get('downvotes', 0)
        total_upvotes += post.get('upvotes', 0)
        total_downvotes += post.get('downvotes', 0)
    
    return render_template(
        'profile.html',
        user=user,
        posts=user_posts,
        total_contribution=total_contribution,
        total_upvotes=total_upvotes,
        total_downvotes=total_downvotes,
        page=page,
        per_page=per_page,
        total_posts=total_posts
    )


@app.route('/notifications')
def notifications_page():
    if 'username' not in session:
        return redirect(url_for('login'))
    
    # Pagination logic
    page = request.args.get('page', 1, type=int)
    per_page = 40
    skip = (page - 1) * per_page

    all_notifications = list(notifications.find().sort('timestamp', -1).skip(skip).limit(per_page))
    
    unseen_count = notifications.count_documents({'seen': False})
    
    return render_template(
        'notification.html',
        notifications=all_notifications,
        page=page,
        per_page=per_page,
        total_notifications=notifications.count_documents({}),
        unseen_count=unseen_count  # Pass the unseen count to the template
    )


@app.route('/dashboard')
def dashboard():
    if 'username' not in session:
        return redirect(url_for('login'))

    # Pagination logic for Approved Topics
    topics_page = request.args.get('topics_page', 1, type=int)
    topics_per_page = 5
    topics_skip = (topics_page - 1) * topics_per_page

    approved_topics = list(posts.find({'status': 'approved'}, {'title': 1}).skip(topics_skip).limit(topics_per_page))
    total_topics = posts.count_documents({'status': 'approved'})

    # Pagination logic for User Profiles
    profiles_page = request.args.get('profiles_page', 1, type=int)
    profiles_per_page = 5
    profiles_skip = (profiles_page - 1) * profiles_per_page

    all_users = list(users.aggregate([
        {
            '$addFields': {
                'role_order': {
                    '$switch': {
                        'branches': [
                            {'case': {'$eq': ['$role', 'admin']}, 'then': 2},
                            {'case': {'$eq': ['$role', 'moderator']}, 'then': 1},
                            {'case': {'$eq': ['$role', 'user']}, 'then': 0}
                        ],
                        'default': -1
                    }
                }
            }
        },
        {
            '$sort': {'role_order': -1}
        },
        {
            '$skip': profiles_skip
        },
        {
            '$limit': profiles_per_page
        }
    ]))

    total_users = users.count_documents({})

    # Post statistics
    post_stats = {
        'approved': posts.count_documents({'status': 'approved'}),
        'pending': posts.count_documents({'status': 'pending'}),
        'rejected': posts.count_documents({'status': 'rejected'}),
        'total': posts.count_documents({})
    }

    # User activity counts
    user_activity = {
        'comments': comments.count_documents({}),
        'upvotes': sum(post.get('upvotes', 0) for post in posts.find()),
        'downvotes': sum(post.get('downvotes', 0) for post in posts.find())
    }

    # Get current time once to ensure consistency
    now = datetime.utcnow()
    
    # Traffic data - count distinct active users in different time periods
    traffic_data = {
        'last_3h': users.count_documents({
            'last_active': {'$gte': now - timedelta(hours=3)}
        }),
        'last_24h': users.count_documents({
            'last_active': {'$gte': now - timedelta(hours=24)}
        }),
        'last_7d': users.count_documents({
            'last_active': {'$gte': now - timedelta(days=7)}
        })
    }

    # User activity chart data - active users per day for last 7 days
    user_activity_chart = {'labels': [], 'data': []}
    
    for i in range(6, -1, -1):  # 6 days ago to today
        day_start = now - timedelta(days=i+1)
        day_end = now - timedelta(days=i)
        
        # Format label as short day name (Mon, Tue, etc.)
        label = day_end.strftime('%a')
        user_activity_chart['labels'].append(label)
        
        # Count users active during this day
        count = users.count_documents({
            'last_active': {
                '$gte': day_start,
                '$lt': day_end
            }
        })
        user_activity_chart['data'].append(count)

    # Get pending posts for moderators
    pending_posts = list(posts.find({'status': 'pending'})) if is_moderator() else []

    return render_template(
        'dashboard.html',
        post_stats=post_stats,
        user_activity=user_activity,
        approved_topics=approved_topics,
        total_topics=total_topics,
        topics_page=topics_page,
        topics_per_page=topics_per_page,
        all_users=all_users,
        total_users=total_users,
        profiles_page=profiles_page,
        profiles_per_page=profiles_per_page,
        pending_posts=pending_posts,
        user_activity_chart=user_activity_chart,
        traffic_data=traffic_data
    )



@app.route('/dashboard/assign_moderator', methods=['POST'])
def assign_moderator_dashboard():
    if not is_admin():
        flash('Permission denied.', 'error')
        return redirect(url_for('dashboard'))
    
    username = request.form.get('username')
    if not username:
        flash('No username selected.', 'error')
        return redirect(url_for('dashboard'))
    
    user = users.find_one({'username': username})
    if not user:
        flash('User not found.', 'error')
        return redirect(url_for('dashboard'))
    
    users.update_one({'username': username}, {'$set': {'role': 'moderator'}})
    
    notification_message = f"🚀 {session['username']} assigned {username} as moderator"
    notification_link = url_for('profile', username=username)
    
    notification = {
        'type': 'moderator_assigned',
        'message': notification_message,
        'link': notification_link,
        'timestamp': datetime.now()
    }
    notifications.insert_one(notification)
    
    flash(f'{username} has been assigned as a moderator.', 'success')
    return redirect(url_for('dashboard'))



@app.route('/mark_all_notifications_seen')
def mark_all_notifications_seen():
    if 'username' not in session:
        return redirect(url_for('login'))
    
    notifications.update_many(
        {'seen': False},
        {'$set': {'seen': True}}
    )
    
    flash('All notifications marked as seen.', 'success')
    return redirect(url_for('notifications_page'))




# again code
@app.route('/dashboard/assign_moderator', methods=['POST'])
def dashboard_assign_moderator():
    if not is_admin():
        flash('Permission denied.', 'error')
        return redirect(url_for('dashboard'))
    
    username = request.form.get('username')
    if not username:
        flash('No username selected.', 'error')
        return redirect(url_for('dashboard'))
    
    user = users.find_one({'username': username})
    if not user:
        flash('User not found.', 'error')
        return redirect(url_for('dashboard'))
    
    users.update_one({'username': username}, {'$set': {'role': 'moderator'}})
    profile_link = url_for('profile', username=user['username'], _external=True)
    
    add_notification(
        'moderator_assigned',
        f"🚀 {session['username']} assigned <a href='{profile_link}'>{username}</a> as moderator",
        target_user=user
    )
    
    flash(f'{username} assigned as moderator.', 'success')
    return redirect(url_for('dashboard'))



@app.route('/dashboard/approve_reject')
def dashboard_approve_reject():
    if not is_moderator():
        flash('You do not have permission to access this page.', 'error')
        return redirect(url_for('home'))
    
    pending_posts = list(posts.find({'status': 'pending'}))
    for post in pending_posts:
        post['author'] = users.find_one({'username': post['username']})
    
    return render_template('dashboard_approve_reject.html', posts=pending_posts)


@app.route('/dashboard/topics')
def dashboard_topics():
    if not is_admin() and not is_moderator():
        flash('You do not have permission to access this page.', 'error')
        return redirect(url_for('home'))
    
    approved_posts = posts.find({'status': 'approved'})
    return render_template('dashboard_topics.html', posts=approved_posts)


@app.route('/dashboard/profiles')
def dashboard_profiles():
    if not is_admin() and not is_moderator():
        flash('You do not have permission to access this page.', 'error')
        return redirect(url_for('home'))
    
    all_users = users.find()
    return render_template('dashboard_profiles.html', users=all_users)




@app.route('/approve_post/<post_id>')
def approve_post(post_id):
    post = posts.find_one({'_id': ObjectId(post_id)})
    posts.update_one({'_id': post['_id']}, {'$set': {'status': 'approved'}})
    add_notification(
        'post_approved',
        f"✅ {post['username']}'s post '{post['title']}' was approved",
        target_post=post
    )
    return redirect(url_for('dashboard'))



@app.route('/reject_post/<post_id>')
def reject_post(post_id):
    if not is_moderator():
        flash('Permission denied.', 'error')
        return redirect(url_for('home'))

    posts.update_one(
        {'_id': ObjectId(post_id)},
        {'$set': {'status': 'rejected'}}
    )

    post = posts.find_one({'_id': ObjectId(post_id)})
    add_notification(f"{session['username']} rejected the post: {post['title']}")

    flash('Post rejected successfully!', 'success')
    return redirect(url_for('dashboard_topics'))


@app.route('/bulk_actions', methods=['POST'])
def bulk_actions():
    if not is_moderator():
        flash('Permission denied.', 'error')
        return redirect(url_for('dashboard'))
    
    post_ids = request.form.getlist('post_ids')
    action = request.form.get('action')
    
    if action == 'approve':
        for pid in post_ids:
            post = posts.find_one({'_id': ObjectId(pid)})
            if post:
                posts.update_one(
                    {'_id': ObjectId(pid)},
                    {'$set': {'status': 'approved'}}
                )
                
                add_notification(
                    'post_approved',
                    f"✅ {post['username']}'s post '{post['title']}' was approved",
                    target_post=post
                )
        
        flash(f'Approved {len(post_ids)} posts.', 'success')
    
    elif action == 'reject':
        for pid in post_ids:
            post = posts.find_one({'_id': ObjectId(pid)})
            if post:
                posts.update_one(
                    {'_id': ObjectId(pid)},
                    {'$set': {'status': 'rejected'}}
                )
                
        flash(f'Rejected {len(post_ids)} posts.', 'success')
    
    return redirect(url_for('dashboard'))



@app.route('/post/<post_id>')
def view_post(post_id):
    if 'username' not in session:
        return redirect(url_for('login'))
    
    post = posts.find_one({'_id': ObjectId(post_id)})
    if not post:
        flash('Post not found.', 'error')
        return redirect(url_for('dashboard'))
    
    if post['status'] != 'approved' and not is_moderator() and post['username'] != session['username']:
        flash('You do not have permission to view this post.', 'error')
        return redirect(url_for('dashboard'))
    
    post_comments = list(comments.find({'post_id': ObjectId(post_id)}))
    
    return render_template('view_post.html', post=post, comments=post_comments)


@app.route('/search', methods=['POST'])
def search():
    if 'username' not in session:
        return redirect(url_for('login'))
    
    query = request.form['query']
    search_type = request.form['search_type']
    
    if search_type == 'topic':
        if is_moderator() or is_admin():
            results = posts.find({'title': {'$regex': query, '$options': 'i'}})
        else:
            results = posts.find({'title': {'$regex': query, '$options': 'i'}, 'status': 'approved'})
        return render_template('search_results.html', results=results, search_type='topic')
    
    elif search_type == 'email':
        user = users.find_one({'email': query})
        if user:
            if is_moderator() or is_admin():
                results = posts.find({'username': user['username']})
            else:
                results = posts.find({'username': user['username'], 'status': 'approved'})
            return render_template('search_results.html', results=results, search_type='email', user=user)
        else:
            flash('User not found', 'error')
            return redirect(url_for('home'))
    
    return redirect(url_for('home'))


@app.route('/logout')
def logout():
    session.pop('username', None)
    return redirect(url_for('login'))

# Register the custom filter
app.jinja_env.filters['make_links_clickable'] = make_links_clickable

if __name__ == '__main__':
    app.run(debug=True)