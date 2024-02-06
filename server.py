"""Server for running helper app."""

import os 
import requests
from flask import Flask, flash, render_template, request, redirect, session, jsonify 
from flask_mail import Mail, Message
from celery import Celery, Task
import logging 
from flask_login import LoginManager, login_user, logout_user
import crud
from model import db, connect_to_db

app = Flask(__name__)
app.secret_key = os.environ['FLASK_KEY']

# flask-login setup
login_manager = LoginManager()
login_manager.init_app(app)

# Celery setup 
def celery_init_app(app: Flask) -> Celery:
    class FlaskTask(Task):
        def __call__(self, *args: object, **kwargs: object) -> object:
            with app.app_context():
                return self.run(*args, **kwargs)

    celery_app = Celery(app.name, task_cls=FlaskTask)
    celery_app.config_from_object(app.config["CELERY"])
    celery_app.set_default()
    app.extensions["celery"] = celery_app
    return celery_app

app.config.from_mapping(
    CELERY=dict(
        broker_url="redis://localhost",
        result_backend="redis://localhost"
    ),
)
celery = celery_init_app(app)
celery.log.setup(loglevel=logging.DEBUG)

# Retrieve secrets 
CLIENT_ID = os.environ['CLIENT_ID']
CLIENT_SECRET = os.environ['CLIENT_SECRET']
REDIRECT_URI = os.environ['REDIRECT_URI']
STRAVA_VERIFY_TOKEN = os.environ['STRAVA_VERIFY_TOKEN']

# Strava endpoints 
BASE_URL = 'https://www.strava.com/api/v3'
AUTHORIZE_URL = 'https://www.strava.com/oauth/authorize'
TOKEN_URL = 'https://www.strava.com/api/v3/oauth/token'
DEAUTHORIZE_URL = 'https://www.strava.com/oauth/deauthorize'

# Permission scopes for Strava authentication 
SCOPES = 'activity:read_all'

# flask-mail configuration
app.config['MAIL_SERVER']='smtp.gmail.com'
app.config['MAIL_PORT'] = 465
app.config['MAIL_USERNAME'] = 'stravagearupdater@gmail.com'
app.config['MAIL_PASSWORD'] = os.environ['EMAIL_PASS']
app.config['MAIL_USE_TLS'] = False
app.config['MAIL_USE_SSL'] = True
mail = Mail(app)

@app.route('/')
def home():
     return render_template('index.html')

@app.route('/update-gear')
def updateGear():
    return render_template('update-gear.html')

@login_manager.user_loader
def load_user(user_id):
    return crud.get_user_by_id(user_id)

@app.route('/returning-user')
def display_login():
    return render_template('login.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        user = crud.get_user_by_email(email) 
        if user and crud.check_password(user, password):
            login_user(user)
            flash("logged in!")
            return redirect('/update-gear/strava-auth')
    return render_template('login.html')

@app.route('/logout')
def logout():
    logout_user()
    flash("logged out!")
    return redirect('/')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        new_user = crud.create_user(email, password)
        db.session.add(new_user)
        db.session.commit()
        return redirect('/login')
    return render_template('register.html')

@app.route('/update-gear/strava-auth')
def authenticate():
    return redirect(f'{AUTHORIZE_URL}?client_id={CLIENT_ID}&redirect_uri={REDIRECT_URI}&response_type=code&scope={SCOPES}')

@app.route('/update-gear/callback')
def callback():
    err = request.args.get('error', '')
    if err: 
        flash("Can't set up gear updater without your Strava authentication")
        return redirect('/update-gear')
    
    # Handle the callback from Strava after user authorization
    code = request.args.get('code')
    scopes = request.args.get('scope')

    # Exchange the authorization code for access and refresh tokens
    data = {
        'client_id': CLIENT_ID,
        'client_secret': CLIENT_SECRET,
        'code': code, # obtained from redirect 
        'grant_type': 'authorization_code', # always 'authorization_code' for initial authentication
    }
    
    response = requests.post(TOKEN_URL, data=data)

    if response.status_code == 200:
        token_data = response.json()
        print(token_data)
        # TODO move to db
        expiration = token_data['expires_at']
        session['access_token'] = token_data['access_token']
        session['refresh_token'] = token_data['refresh_token']
        print('your session data is as follows')
        print(session)
        return f'Authentication successful! Welcome {token_data["athlete"]["firstname"]}!'

    return 'Authentication failed.'

@app.route('/webhook', methods=['GET','POST'])
def webhook():
    if request.method == 'GET':
        print("hello :)")
        hub_challenge = request.args.get('hub.challenge', '')
        hub_verify_token = request.args.get('hub.verify_token', '')
        if hub_verify_token == STRAVA_VERIFY_TOKEN:
            print("we out here")
            return jsonify({'hub.challenge': hub_challenge})
        elif hub_verify_token:
            print("we here actually")
            return 'Invalid verify token', 403
        else:
            print("there's a problem")
    elif request.method == 'POST':
        data = request.get_json()
        print("we got a post request on our webhook")
        process_new_activity.delay(data)
        return jsonify({"status": "success"})
    else:
        return "Invalid request"

@celery.task
def process_new_activity(data):
    activity_id = data['object_id']
    print(f'hey! thanks for telling me to process activity {activity_id}')
    if 'access_token' in session:
        print("yea!")
    access_token = "a51ca2b9c4d8342b8289f27a8c270369dc4abd40"
    headers = {'Authorization': f'Bearer {access_token}'}
    params = {'include_all_efforts': False}
    resp = requests.get(f'{BASE_URL}/activities/{activity_id}', headers=headers, params=params)
    respData = resp.json()
    gearDeets = respData['gear']
    print(f'hello? {gearDeets}')
    if respData['gear']['primary'] == True: 
        print("you used your primary gear! no email needed :)")
    else:
        print("this should fire an email to check your gear!")
    # else: 
    #     print("can't process activity without access token!")

def send_email():
    msg = Message('Hello from strava gear updater', sender = 'stravagearupdater@gmail.com', recipients = ['charlotte.bourg@gmail.com'])
    msg.body = "testing from flask-mail"
    mail.send(msg)
    return "sent"

if __name__ == '__main__':
    connect_to_db(app)
    app.run('0.0.0.0', debug=True)
    app.app_context().push()